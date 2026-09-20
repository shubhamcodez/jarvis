"""FastAPI backend: chat, send_message (classify + agent/chat), chat log, storage, WebSocket for agent steps."""
from __future__ import annotations

import asyncio
import queue
import sys
import threading
import time
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Windows: Proactor is required for asyncio subprocesses (e.g. shell tools). Without it,
# SelectorEventLoop is used and create_subprocess_exec raises NotImplementedError.
# We set the policy on all Windows versions we support; ignore if the API is removed later.
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except AttributeError:
        pass

# LangChain still imports Pydantic v1 shims; noisy on 3.14 until upstream finishes the migration.
warnings.filterwarnings(
    "ignore",
    message=r"Core Pydantic V1 functionality isn't compatible with Python 3\.14 or greater\.",
    category=UserWarning,
    module=r"langchain_core\._api\.deprecation",
)

import json

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import (
    api_keys_status,
    get_autonomy_level,
    get_chat_history_limit,
    get_grep_root,
    get_llm_api_key,
    get_llm_provider,
    get_local_model_id,
    get_quiet_hours,
    in_quiet_hours,
    get_run_mode,
    get_spend_limits,
    set_local_model_id,
    get_openai_api_key,
    is_desktop_armed,
    is_packaged,
    set_autonomy_level,
    set_desktop_armed,
    set_llm_provider,
    set_quiet_hours,
    set_run_mode,
    set_spend_limits,
    write_api_keys,
)
from agents.models import get_llm_client
from agents.supervisor import compute_supervisor_decision
from memory import get_memory_store, ingest_chat, run_retrieval_pipeline, schedule_write_back
from memory.user_profile_io import read_user_profile, write_user_profile
from memory.chat_log import (
    InvalidChatId,
    append_chat_log,
    chat_exists,
    create_new_chat,
    delete_chat,
    get_current_chat_id,
    is_valid_chat_id,
    list_chats,
    read_chat_log,
    set_current_chat,
)
from storage import get_chats_storage_path, set_chats_storage_path
from agents.router import create_router_graph
from observability.trace import trace_log, list_traces
from observability.auto_loop import schedule_post_turn_observability
from observability.feedback_assess import (
    format_feedback_assessment_markdown,
    is_feedback_complaint,
    run_feedback_assessment,
)
from observability.eval_gen import generate_evals_from_logs
from observability.eval_runner import run_evals_for_all_models, pass_at_k
from observability.evals import load_eval_cases, load_eval_runs
from observability.optimize import run_optimization_step, get_latest_optimization_stats
from observability.human_eval import run_human_eval_benchmark
from tools import get_weather, try_weather_tool
from tools.python_sandbox import run_sandboxed_python
from tools.sandbox_markdown import redact_sandbox_result_dict
from tools.file_grep import grep_files
from tools.shell_runner import is_shell_enabled, run_shell_command
from tools.workspace_file_edits import extract_workspace_file_edits
from auth.google_oauth import (
    callback_error_redirect,
    callback_success_redirect,
    cookie_secure,
    create_login_url,
    disconnect_session,
    exchange_code_and_create_session,
    get_valid_access_token_for_session,
    google_status_by_session,
    logout_session,
    oauth_client_id_hint,
    oauth_missing_config_fields,
    oauth_redirect_uri,
    oauth_suggested_javascript_origin,
)
from integrations.gmail_client import fetch_gmail_profile
from custom_agents.api import router as custom_agents_router
from custom_agents.context import build_agent_system_prompt, filter_supervisor_plan
from custom_agents.runtime import remember_note_from_message
from custom_agents.store import append_memory_note, resolve_agent

_GOOGLE_SID_COOKIE = "ada_google_sid"
_LEGACY_GOOGLE_SID_COOKIE = "jarvis_google_sid"


def _google_session_cookie(request: Request) -> str | None:
    sid = request.cookies.get(_GOOGLE_SID_COOKIE) or request.cookies.get(
        _LEGACY_GOOGLE_SID_COOKIE
    )
    return sid if sid else None


def _clear_google_sid_cookies(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(_GOOGLE_SID_COOKIE, path="/")
    response.delete_cookie(_LEGACY_GOOGLE_SID_COOKIE, path="/")


def _boot_hardware_and_runtime() -> None:
    """First-launch probe so any packaged install sizes a local model for this machine."""
    try:
        from agents.hardware import detect_hardware

        detect_hardware(force=True)
        from agents.local_models import public_status
        from config import get_local_model_id, set_local_model_id

        if not get_local_model_id():
            sid = (public_status() or {}).get("suggested_model_id")
            if sid:
                set_local_model_id(sid)
    except Exception:
        return
    try:
        from agents.llama_cpp_bin import ensure_binary

        ensure_binary()
    except Exception:
        pass


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    from custom_agents.scheduler import scheduler_loop

    threading.Thread(target=_boot_hardware_and_runtime, daemon=True).start()
    try:
        from agents.tasks import pause_inflight

        pause_inflight()
    except Exception:
        pass
    try:
        from observability.struct_log import configure_struct_logging

        configure_struct_logging()
    except Exception:
        pass
    try:
        from auth.local_token import get_or_create_token

        get_or_create_token()
    except Exception:
        pass
    try:
        get_memory_store()
    except Exception:
        pass
    try:
        from observability.metrics import flush as flush_metrics

        flush_metrics(force=True)
    except Exception:
        pass
    sched = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        try:
            from observability.metrics import flush as flush_metrics

            flush_metrics(force=True)
        except Exception:
            pass
        sched.cancel()
        try:
            await sched
        except asyncio.CancelledError:
            pass


_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "http://localhost:1430",
    "http://127.0.0.1:1430",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
)
_AUTH_PUBLIC_PATHS = frozenset({"/health", "/auth/google/callback"})

app = FastAPI(
    title="Jarvis API",
    lifespan=_lifespan,
    docs_url=None if is_packaged() else "/docs",
    redoc_url=None if is_packaged() else "/redoc",
    openapi_url=None if is_packaged() else "/openapi.json",
)
app.include_router(custom_agents_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _require_local_api_token(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if path in _AUTH_PUBLIC_PATHS:
        return await call_next(request)
    from auth.local_token import request_has_valid_token

    allow_query = request.method == "GET" and path.startswith("/auth/google/login")
    if request_has_valid_token(request, allow_query=allow_query):
        return await call_next(request)
    origin = (request.headers.get("origin") or "").strip()
    headers = {}
    if origin in _CORS_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    return JSONResponse({"detail": "Unauthorized"}, status_code=401, headers=headers)


# WebSocket connections for desktop-agent-step broadcasts
_ws_connections: list[WebSocket] = []
_ws_lock = asyncio.Lock()
_SENTINEL = object()
_UPLOAD_ROOT = Path(__import__("tempfile").gettempdir()) / "jarvis-uploads"


def _jail_attachment_paths(paths: Optional[list[str]]) -> list[str]:
    """Only allow files under the upload temp dir; cap count and size."""
    if not paths:
        return []
    root = _UPLOAD_ROOT.resolve()
    out: list[str] = []
    for raw in paths[:8]:
        try:
            p = Path(raw).expanduser().resolve()
            p.relative_to(root)
        except (OSError, ValueError):
            continue
        if not p.is_file():
            continue
        try:
            if p.stat().st_size > 10 * 1024 * 1024:
                continue
        except OSError:
            continue
        out.append(str(p))
    return out


def _history_with_current(hist: Optional[list], message: str) -> Optional[list]:
    """Ensure the current user turn is the last history item sent to the model."""
    msg = (message or "").strip()
    rows = list(hist or [])
    if not msg:
        return rows or None
    if rows:
        last = rows[-1]
        if last.get("role") == "user" and (last.get("content") or "").strip() == msg:
            return rows
    rows.append({"role": "user", "content": msg})
    return rows


def _persist_agent_step(step, thought, action, description, result, done) -> None:
    try:
        from observability.actions import log_agent_action

        log_agent_action(
            step=step,
            action=action or "",
            description=description or "",
            thought=thought or "",
            result=result,
            done=bool(done),
        )
    except Exception:
        pass


def _require_chat_id(chat_id: str) -> str:
    try:
        if not is_valid_chat_id(chat_id):
            raise InvalidChatId("invalid chat id")
        return chat_id
    except InvalidChatId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sse_data(obj: dict) -> str:
    """One SSE event line (JSON payload)."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _strip_workspace_edits_from_reply(reply: str) -> tuple[str, list | None]:
    """Remove ```jarvis-file:...``` blocks from visible reply; return pending edits for UI."""
    clean, edits = extract_workspace_file_edits(reply or "")
    return clean, edits if edits else None


def _maybe_append_goal_critic(chat_id: Optional[str], message: str, reply: str, tool_used) -> str:
    """When `/goal` is set, run the critic once against that condition after the turn."""
    if not chat_id or not (reply or "").strip():
        return reply
    try:
        from memory.chat_log import get_chat_meta

        cond = (get_chat_meta(chat_id).get("goal_condition") or "").strip()
        if not cond:
            return reply
        from agents.swe_loop import critic_evaluate
        from config import get_llm_api_key, get_llm_provider

        tests = None
        changed: list = []
        tu = tool_used if isinstance(tool_used, dict) else {}
        raw = tu.get("result")
        parsed = raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {}
        if isinstance(parsed, dict):
            t = parsed.get("tests")
            tests = t if isinstance(t, dict) else None
            changed = list(parsed.get("changed") or [])
        critic = critic_evaluate(
            get_llm_api_key(),
            get_llm_provider(),
            cond,
            changed,
            tests,
            None,
            f"User: {(message or '')[:400]}\nReply: {(reply or '')[:1500]}",
        )
        line = f"\n\n**Goal check** (`{cond}`): passed={critic.get('passed')} score={critic.get('score')}"
        issues = critic.get("issues") or []
        if issues:
            line += " — " + "; ".join(str(x) for x in issues[:3])
        retry = (critic.get("retry_instructions") or "").strip()
        if retry and not critic.get("passed"):
            line += f"\n_{retry[:400]}_"
        return reply + line
    except Exception:
        return reply


def _agent_step_for_sse(payload: dict) -> dict:
    """Omit huge base64 screenshots from SSE JSON (WebSocket still carries them)."""
    return {
        "type": "agent_step",
        "step": payload.get("step"),
        "thought": (payload.get("thought") or "")[:2000],
        "action": payload.get("action") or "",
        "description": (payload.get("description") or "")[:2000],
        "result": (payload.get("result") or "")[:1500] if payload.get("result") else None,
        "done": bool(payload.get("done")),
    }


# Lazy-compiled router graph (LangGraph)
_router_graph = None


def _get_router_graph():
    global _router_graph
    if _router_graph is None:
        _router_graph = create_router_graph()
    return _router_graph


async def _emit_agent_step(
    step: int,
    thought: str,
    action: str,
    description: str,
    result: Optional[str],
    done: bool,
    screenshot: Optional[str] = None,
):
    payload = {
        "step": step,
        "thought": thought,
        "action": action,
        "description": description,
        "result": result,
        "done": done,
    }
    try:
        from agents.run_control import current_chat_id, current_run_id

        cid = current_chat_id()
        rid = current_run_id()
        if cid:
            payload["chat_id"] = cid
        if rid:
            payload["run_id"] = rid
    except Exception:
        pass
    if screenshot is not None:
        payload["screenshot"] = screenshot
    async with _ws_lock:
        conns = list(_ws_connections)
    dead: list[WebSocket] = []
    for ws in conns:
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=2.0)
        except Exception:
            dead.append(ws)
    if dead:
        async with _ws_lock:
            for ws in dead:
                if ws in _ws_connections:
                    _ws_connections.remove(ws)


# --- Pydantic models ---
class SendMessageRequest(BaseModel):
    message: str = ""
    attachment_paths: Optional[list[str]] = None
    chat_id: Optional[str] = None
    web_search_query: Optional[str] = None
    coding_mode: bool = False
    coding_project_snapshot: Optional[str] = None  # browser-built index from open folder
    custom_agent_id: Optional[str] = None
    resume_task_id: Optional[str] = None


class ChatbotResponseRequest(BaseModel):
    message: str = ""
    attachment_paths: Optional[list[str]] = None
    web_search_query: Optional[str] = None


def _prepare_coding_project_context(coding_mode: bool, snapshot: Optional[str] = None) -> str:
    from tools.project_rules import load_project_rules

    rules = load_project_rules()
    text = ""
    if coding_mode:
        text = (snapshot or "").strip()
        if not text:
            try:
                from tools.workspace_io import snapshot as ws_snapshot

                built = ws_snapshot()
                if built.get("ok"):
                    text = (built.get("snapshot") or "").strip()
            except Exception:
                pass
    if rules and text:
        return rules + "\n\n" + text
    return rules or text


def _custom_agent_for_turn(custom_agent_id: Optional[str], chat_id: Optional[str], message: str):
    """Load a custom agent (if any) and optionally capture an explicit remember note."""
    profile = resolve_agent(custom_agent_id, chat_id)
    if not profile:
        return None
    note = remember_note_from_message(message or "")
    if note:
        try:
            append_memory_note(profile["id"], note)
        except Exception:
            pass
        try:
            from memory.facts import add_fact

            add_fact(note, source="remember", confidence=0.9)
        except Exception:
            pass
    return profile


def _schedule_turn_writeback(chat_id: Optional[str], message: str, reply: str = "") -> None:
    try:
        from agents.run_control import current_run_id, is_cancelled

        rid = current_run_id()
        if rid and is_cancelled(rid):
            return
    except Exception:
        pass
    try:
        schedule_write_back(
            chat_id=chat_id or "",
            user_message=message or "",
            assistant_reply=reply or "",
        )
    except Exception:
        pass


def _merge_custom_agent_system(sys_content: Optional[str], profile: Optional[dict], message: str) -> Optional[str]:
    if not profile:
        return sys_content
    agent_sys = build_agent_system_prompt(profile, message or "")
    if agent_sys and sys_content:
        return agent_sys + "\n\n" + sys_content
    return agent_sys or sys_content


class AppendChatLogRequest(BaseModel):
    role: str
    content: str
    chat_id: Optional[str] = None


class SetCurrentChatRequest(BaseModel):
    chat_id: str


class FeedbackAssessRequest(BaseModel):
    chat_id: str


class SetStoragePathRequest(BaseModel):
    path: str


class SetModelRequest(BaseModel):
    provider: str  # "openai", "xai", or "local"
    local_model_id: Optional[str] = None


class LocalModelRequest(BaseModel):
    model_id: str


class SetAutonomyRequest(BaseModel):
    autonomy: str


class SetDesktopArmedRequest(BaseModel):
    armed: bool


class SetApiKeysRequest(BaseModel):
    openai_api_key: Optional[str] = None
    xai_api_key: Optional[str] = None


class WorkspaceLinkRequest(BaseModel):
    path: str


class WorkspaceWriteRequest(BaseModel):
    rel_path: str
    content: str = ""


class WorkspaceRunRequest(BaseModel):
    """Run one file under the linked workspace (VS Code–style Run; does not require host shell)."""

    rel_path: str
    timeout_sec: float | None = None


class AgentApproveRequest(BaseModel):
    approval_id: str
    approve: bool = True
    chat_id: Optional[str] = None


def _trace_extra(result: dict | None) -> dict:
    result = result or {}
    extra = {}
    if result.get("run_id"):
        extra["run_id"] = result.get("run_id")
    if result.get("failure_class"):
        extra["failure_class"] = result.get("failure_class")
    spec = result.get("task_spec") or (result.get("agent_state") or {}).get("task_spec")
    if spec:
        extra["task_spec"] = {
            "goal": spec.get("goal"),
            "risk": spec.get("risk"),
            "success_criteria": spec.get("success_criteria"),
        }
    plan = (result.get("agent_state") or {}).get("plan")
    if plan:
        extra["plan"] = plan
    return extra


class PythonSandboxRequest(BaseModel):
    """Run Python in an isolated subprocess with restricted builtins (see tools/sandbox_worker.py, SANDBOX.md)."""

    code: str
    timeout_sec: float = 15.0


class ShellRunRequest(BaseModel):
    """Run one host shell command on the host (see tools/shell_runner.py; disable with ADA_ENABLE_SHELL=0)."""

    command: str
    timeout_sec: float | None = None


class WebSearchRequest(BaseModel):
    query: str


# --- Chat ---
@app.post("/chat/response")
async def chatbot_response(body: ChatbotResponseRequest):
    """Chat only (no classification)."""
    provider = get_llm_provider()
    api_key = get_llm_api_key()
    client = get_llm_client(provider)
    msg = (body.message or "").strip()
    paths = body.attachment_paths or []
    ws_q = (body.web_search_query or "").strip() or None
    if not msg and paths:
        msg = "Please summarize or answer based on the attached documents."
    if not msg and ws_q:
        msg = f"Summarize and answer based on a web search about: {ws_q}"
    trace_msg = msg
    reply = ""
    tool_used = None
    t0 = time.perf_counter()
    from agents.run_control import finish_run, start_run, set_current_ids

    cr = start_run(chat_id="", task_id="")
    cr_id = cr.get("run_id")
    set_current_ids(run_id=cr_id)
    try:
        from tools.runner import run_tools_for_turn
        from memory.prompt_assembly import assemble_turn_context
        from observability.spans import span

        with span("turn", route="chat", provider=provider):
            tool_sys, tool_used = await asyncio.to_thread(
                run_tools_for_turn, msg, None, ws_q
            )
            pack = assemble_turn_context(
                user_message=msg,
                tool_system=tool_sys or "",
                untrusted_tools=bool(ws_q),
            )
            reply = await asyncio.to_thread(
                client.chat,
                api_key,
                pack.user_message or msg,
                paths if paths else None,
                pack.history or None,
                pack.system or None,
            )
        trace_log(
            provider=provider,
            route="chat",
            message=trace_msg,
            reply=reply,
            success=True,
            duration_sec=time.perf_counter() - t0,
        )
        schedule_post_turn_observability()
        _schedule_turn_writeback(None, msg, reply)
        finish_run(cr_id)
    except Exception as e:
        finish_run(cr_id, "error")
        trace_log(
            provider=provider,
            route="chat",
            message=trace_msg,
            reply="",
            success=False,
            error=str(e),
            duration_sec=time.perf_counter() - t0,
        )
        raise
    out: dict = {"reply": reply, "run_id": cr_id}
    if tool_used:
        out["tool_used"] = tool_used
    return out


@app.post("/chat/send-message")
async def send_message(body: SendMessageRequest, request: Request):
    """
    Main entry: LangGraph router classifies then routes to chat, desktop, coding (sandbox: numpy/pandas/matplotlib/yfinance), shell (opt-in), finance (yfetch + prose), or google (Calendar/Gmail API).
    """
    provider = get_llm_provider()
    api_key = get_llm_api_key()
    message = (body.message or "").strip()
    attachment_paths = _jail_attachment_paths(body.attachment_paths)
    ws_q = (body.web_search_query or "").strip()
    if not message and ws_q:
        message = f"Summarize and answer based on a web search about: {ws_q}"
    if (
        not message
        and body.coding_mode
        and (body.coding_project_snapshot or "").strip()
    ):
        message = (
            "Give a concise overview of this imported project: structure, main technologies, and entry points."
        )
    chat_id = body.chat_id

    if chat_id and is_feedback_complaint(message):
        from agents.run_control import finish_run, start_run

        fb_run = start_run(chat_id=chat_id or "", task_id="")
        fb_id = fb_run.get("run_id")
        t_fb = time.perf_counter()
        try:
            result = await asyncio.to_thread(run_feedback_assessment, chat_id, provider)
            reply = format_feedback_assessment_markdown(result)
            trace_log(
                provider=provider,
                route="feedback_assess",
                message=message,
                reply=reply[:4000],
                success=bool(result.get("ok")),
                error=result.get("error") if not result.get("ok") else None,
                duration_sec=time.perf_counter() - t_fb,
            )
            schedule_post_turn_observability()
            finish_run(fb_id)
            return {"reply": reply, "tool_used": None, "run_id": fb_id}
        except Exception as e:
            finish_run(fb_id, "error")
            trace_log(
                provider=provider,
                route="feedback_assess",
                message=message,
                reply="",
                success=False,
                error=str(e),
                duration_sec=time.perf_counter() - t_fb,
            )
            raise

    step_queue: queue.Queue = queue.Queue()

    def on_step(step, thought, action, description, result, done, screenshot_base64=None):
        _persist_agent_step(step, thought, action, description, result, done)
        step_queue.put({
            "step": step, "thought": thought or "", "action": action or "",
            "description": description or "", "result": result, "done": done,
            "screenshot": screenshot_base64,
        })

    async def drain_steps():
        loop = asyncio.get_running_loop()
        while True:
            try:
                payload = await loop.run_in_executor(None, step_queue.get)
            except Exception:
                break
            if payload is _SENTINEL:
                break
            await _emit_agent_step(
                payload["step"], payload["thought"], payload["action"],
                payload["description"], payload.get("result"), payload.get("done", False),
                payload.get("screenshot"),
            )

    coding_ctx = await asyncio.to_thread(
        _prepare_coding_project_context,
        body.coding_mode,
        body.coding_project_snapshot,
    )
    custom_profile = _custom_agent_for_turn(body.custom_agent_id, chat_id, message)
    custom_sys = (
        build_agent_system_prompt(custom_profile, message) if custom_profile else None
    )
    from agents.run_control import cancel_run, finish_run, is_cancelled, start_run, set_current_ids

    ns_run = start_run(chat_id=chat_id or "", task_id="")
    ns_run_id = ns_run.get("run_id")
    initial_state = {
        "message": message,
        "attachment_paths": attachment_paths,
        "chat_id": chat_id,
        "api_key": api_key,
        "provider": provider,
        "on_step": on_step,
        "web_search_query": ws_q or None,
        "google_session_id": _google_session_cookie(request),
        "coding_mode": bool(body.coding_mode),
        "coding_project_context": coding_ctx,
        "custom_agent_id": custom_profile["id"] if custom_profile else None,
        "custom_agent_system": custom_sys,
        "custom_agent_tools": list(custom_profile.get("tools") or []) if custom_profile else None,
        "resume_task_id": body.resume_task_id,
        "run_id": ns_run_id,
    }
    graph = _get_router_graph()
    drain_task = asyncio.create_task(drain_steps())
    start = time.perf_counter()
    file_edits = None
    result = None
    reply = ""
    cancelled = False
    from observability.spans import span

    set_current_ids(run_id=ns_run_id, chat_id=chat_id or "")

    async def _watch_disconnect():
        while True:
            if await request.is_disconnected():
                cancel_run(ns_run_id, "client_disconnect")
                return
            await asyncio.sleep(1.0)

    watch = asyncio.create_task(_watch_disconnect())
    try:
        with span("turn", route="send-message", chat_id=chat_id or "", provider=provider):
            result = await graph.ainvoke(initial_state)
        cancelled = is_cancelled(ns_run_id)
        if cancelled:
            finish_run(ns_run_id, "cancelled")
            reply = (result or {}).get("reply") or "Stopped."
        else:
            reply, file_edits = _strip_workspace_edits_from_reply(result.get("reply") or "No response.")
            route = result.get("route") or "chat"
            tool_used = result.get("tool_used")
            reply = _maybe_append_goal_critic(chat_id, message, reply, tool_used)
            if tool_used and chat_id:
                set_current_chat(chat_id)
                append_chat_log("tool", json.dumps(tool_used), chat_id=chat_id)
            extra = _trace_extra(result)
            extra["step_count"] = len((result.get("agent_state") or {}).get("plan") or [])
            trace_log(
                provider=provider,
                route=route,
                message=message,
                reply=reply,
                success=True,
                duration_sec=time.perf_counter() - start,
                extra=extra,
            )
            schedule_post_turn_observability()
            _schedule_turn_writeback(chat_id, message, reply)
            finish_run(ns_run_id)
    except Exception as e:
        finish_run(ns_run_id, "error")
        trace_log(
            provider=provider,
            route="chat",
            message=message,
            reply="",
            success=False,
            error=str(e),
            duration_sec=time.perf_counter() - start,
            extra={"failure_class": "infrastructure"},
        )
        raise
    finally:
        watch.cancel()
        try:
            await watch
        except (asyncio.CancelledError, Exception):
            pass
        step_queue.put(_SENTINEL)
        await drain_task
        rec_status = None
        try:
            from agents.run_control import get_run

            rec = get_run(ns_run_id)
            rec_status = rec.get("status") if rec else None
        except Exception:
            pass
        if rec_status == "running":
            finish_run(ns_run_id, "cancelled" if cancelled else "complete")
    out = {"reply": reply, "run_id": ns_run_id}
    if file_edits:
        out["file_edits"] = file_edits
    if isinstance(result, dict) and result.get("tool_used"):
        out["tool_used"] = result["tool_used"]
    if isinstance(result, dict) and result.get("pending_approvals"):
        out["pending_approvals"] = result["pending_approvals"]
    return out


@app.post("/chat/send-message/stream")
async def send_message_stream(body: SendMessageRequest, request: Request):
    """
    Streaming variant: classify first; if chat, stream SSE chunks; else run agent and send one final SSE event.
    """
    google_session_id = _google_session_cookie(request)
    provider = get_llm_provider()
    api_key = get_llm_api_key()
    message = (body.message or "").strip()
    attachment_paths = _jail_attachment_paths(body.attachment_paths)
    ws_q = (body.web_search_query or "").strip()
    if not message and ws_q:
        message = f"Summarize and answer based on a web search about: {ws_q}"
    if (
        not message
        and body.coding_mode
        and (body.coding_project_snapshot or "").strip()
    ):
        message = (
            "Give a concise overview of this imported project: structure, main technologies, and entry points."
        )
    chat_id = body.chat_id
    has_attachments = len(attachment_paths) > 0
    client = get_llm_client(provider)
    coding_ctx = await asyncio.to_thread(
        _prepare_coding_project_context,
        body.coding_mode,
        body.coding_project_snapshot,
    )
    custom_profile = _custom_agent_for_turn(body.custom_agent_id, chat_id, message)
    if not custom_profile:
        note = remember_note_from_message(message or "")
        if note:
            try:
                from memory.facts import add_fact

                add_fact(note, source="remember", confidence=0.9)
            except Exception:
                pass
    from agents.run_control import finish_run, start_run

    stream_run = start_run(chat_id=chat_id or "", task_id="")
    stream_run_id = stream_run.get("run_id")

    async def _stream_chat_reply(
        api_key_,
        msg_,
        paths_,
        history_=None,
        system_content_=None,
        tool_used_=None,
        chat_id_=None,
        provider_=None,
        trace_user_message_=None,
        run_id_=None,
    ):
        """Run sync chat_stream in executor and yield SSE as chunks arrive. Optional tool_used for final event."""
        chunk_queue = queue.Queue()
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()

        def producer():
            try:
                for c in client.chat_stream(
                    api_key_, msg_, paths_, history=history_, system_content=system_content_
                ):
                    chunk_queue.put(c)
            except Exception as e:
                chunk_queue.put(e)
            finally:
                chunk_queue.put(None)

        asyncio.ensure_future(loop.run_in_executor(None, producer))
        full = []
        from agents.run_control import add_tokens, estimate_tokens, is_cancelled, spend_ok

        add_tokens(run_id_, estimate_tokens(msg_ or ""), 0)
        emitted = 0
        while True:
            if run_id_ and is_cancelled(run_id_):
                break
            ok_spend, info = spend_ok(run_id_)
            if run_id_ and not ok_spend:
                yield _sse_data({"type": "usage", **info, "capped": True})
                break
            chunk = await loop.run_in_executor(None, chunk_queue.get)
            if chunk is None:
                break
            if isinstance(chunk, Exception):
                if provider_ is not None and trace_user_message_ is not None:
                    trace_log(
                        provider=provider_,
                        route="chat",
                        message=trace_user_message_,
                        reply="",
                        success=False,
                        error=str(chunk),
                        duration_sec=time.perf_counter() - t0,
                    )
                raise chunk
            full.append(chunk)
            usage = add_tokens(run_id_, 0, estimate_tokens(chunk))
            yield f"data: {json.dumps({'delta': chunk})}\n\n"
            emitted += 1
            if usage and emitted % 8 == 0:
                yield _sse_data({"type": "usage", **usage})
        reply, file_edits = _strip_workspace_edits_from_reply("".join(full))
        cancelled = bool(run_id_ and is_cancelled(run_id_))
        if provider_ is not None and trace_user_message_ is not None:
            trace_log(
                provider=provider_,
                route="chat",
                message=trace_user_message_,
                reply=reply,
                success=not cancelled,
                duration_sec=time.perf_counter() - t0,
                extra={"cancelled": True} if cancelled else None,
            )
            if not cancelled:
                schedule_post_turn_observability()
                _schedule_turn_writeback(chat_id_, trace_user_message_ or msg_, reply)
        payload = {"done": True, "reply": reply}
        if file_edits:
            payload["file_edits"] = file_edits
        if tool_used_:
            payload["tool_used"] = tool_used_
            if chat_id_:
                append_chat_log("tool", json.dumps(tool_used_), chat_id=chat_id_)
        if run_id_:
            payload["run_id"] = run_id_
        yield f"data: {json.dumps(payload)}\n\n"

    def _chat_history_and_system():
        """Load history, retrieve memory, run tools, assemble a token-budgeted system pack."""
        from config import get_openai_api_key
        from memory.prompt_assembly import assemble_turn_context
        from observability.metrics import incr, observe
        from observability.spans import span
        from tools.runner import run_tools_for_turn

        _lim = get_chat_history_limit()
        raw_hist = list(read_chat_log(chat_id)[-_lim:] if chat_id else [])
        recent_for_tools = _history_with_current(raw_hist, message) or []
        memory_context = ""
        with span("retrieval", chat_id=chat_id or "", route="chat") as sp:
            try:
                store = get_memory_store()
                if len(store) > 0:
                    try:
                        key = get_openai_api_key()
                    except ValueError:
                        key = get_llm_api_key()
                    from agents.agent_state import load_state as _load_ast
                    from memory.thread_context import infer_thread_from_turns, merge_thread, thread_from_state

                    _th = merge_thread(
                        thread_from_state(_load_ast(chat_id)),
                        infer_thread_from_turns(recent_for_tools),
                    )
                    memory_context, hits = run_retrieval_pipeline(
                        store,
                        key,
                        current_message=message,
                        recent_turns=recent_for_tools,
                        task_state={
                            "route": _th.get("last_route") or "chat",
                            "goal": _th.get("last_goal") or message,
                        },
                        top_k=8,
                        include_raw_top_n=3,
                        max_memory_raw_chars=1800,
                    )
                    memory_context = (memory_context or "").strip()
                    sp.set(hits=len(hits), store_size=len(store))
                    incr("retrieval.calls")
                    observe("retrieval.hits", float(len(hits)))
            except Exception as exc:
                sp.fail(str(exc))
        allowed = set(custom_profile.get("tools") or []) if custom_profile else None
        with span("tools", chat_id=chat_id or ""):
            tool_system, tool_used = run_tools_for_turn(
                message or "",
                recent_turns=recent_for_tools,
                web_search_query=ws_q or None,
                allowed_tools=allowed,
            )
        custom_sys_text = ""
        if custom_profile:
            custom_sys_text = build_agent_system_prompt(custom_profile, message or "") or ""
        from agents.agent_state import load_state as _load_ast2, structured_view
        from memory.thread_context import (
            format_thread_context,
            infer_thread_from_turns,
            merge_thread,
            thread_from_state,
        )

        ast = _load_ast2(chat_id) if chat_id else {}
        thread = merge_thread(thread_from_state(ast), infer_thread_from_turns(raw_hist))
        pack = assemble_turn_context(
            user_message=message or "",
            history=raw_hist,
            memory_context=memory_context,
            tool_system=tool_system or "",
            custom_agent_system=custom_sys_text,
            untrusted_tools=bool(ws_q),
            agent_state_text=structured_view(ast),
            thread_context_text=format_thread_context(thread),
        )
        sys_final = (pack.system or "").strip() or None
        if (coding_ctx or "").strip():
            inj = (
                "\n\n## Project workspace (linked folder)\n"
                "The client sends a snapshot of the user's **currently open folder**. When they ask you to fix, implement, "
                "or refactor **project** code, output each updated file as a markdown code fence whose **first line** is "
                "exactly `jarvis-file:relative/path/from/root.ext` (then a newline), then the **complete** new file contents "
                "(full file, not a patch), then a closing line ` ``` ` (three backticks) alone. "
                "Use forward slashes; one fence per file. The UI shows a diff and applies changes on the user's machine. "
                "Other code fences are for examples only; only `jarvis-file:` openers become pending workspace edits."
            )
            sys_final = (sys_final + inj) if sys_final else inj.strip()
        try:
            observe("context.system_tokens", float(pack.stats.get("system_tokens") or 0))
            observe("context.stable_tokens", float(pack.stats.get("stable_tokens") or 0))
            observe("context.dynamic_tokens", float(pack.stats.get("dynamic_tokens") or 0))
        except Exception:
            pass
        return pack.history or None, sys_final, tool_used

    async def event_stream():
        yield _sse_data({"type": "run", "run_id": stream_run_id})
        if chat_id and message and is_feedback_complaint(message):
            t_fb = time.perf_counter()
            yield _sse_data(
                {
                    "type": "status",
                    "phase": "feedback_assess",
                    "message": "Reviewing this thread and the alternate model…",
                }
            )
            try:
                result = await asyncio.to_thread(run_feedback_assessment, chat_id, provider)
                reply_md = format_feedback_assessment_markdown(result)
                trace_log(
                    provider=provider,
                    route="feedback_assess",
                    message=message,
                    reply=reply_md[:4000],
                    success=bool(result.get("ok")),
                    error=result.get("error") if not result.get("ok") else None,
                    duration_sec=time.perf_counter() - t_fb,
                )
                schedule_post_turn_observability()
                yield f"data: {json.dumps({'delta': reply_md})}\n\n"
                yield f"data: {json.dumps({'done': True, 'reply': reply_md, 'run_id': stream_run_id})}\n\n"
            except Exception as e:
                err = str(e)
                trace_log(
                    provider=provider,
                    route="feedback_assess",
                    message=message,
                    reply="",
                    success=False,
                    error=err,
                    duration_sec=time.perf_counter() - t_fb,
                )
                fallback = f"Sorry, feedback review failed: {err}"
                yield f"data: {json.dumps({'delta': fallback})}\n\n"
                yield f"data: {json.dumps({'done': True, 'reply': fallback, 'run_id': stream_run_id})}\n\n"
            finish_run(stream_run_id)
            return

        # Attachments-only: go straight to chat stream
        if not message and has_attachments:
            yield _sse_data({"type": "status", "phase": "context", "message": "Loading context and attachments…"})
            msg = "Please summarize or answer based on the attached documents."
            hist, sys, tool_used = await asyncio.to_thread(_chat_history_and_system)
            yield _sse_data({"type": "status", "phase": "stream", "message": "Streaming reply…"})
            async for line in _stream_chat_reply(
                api_key,
                msg,
                attachment_paths,
                hist,
                sys,
                tool_used,
                chat_id,
                provider_=provider,
                trace_user_message_=message,
                run_id_=stream_run_id,
            ):
                if await request.is_disconnected():
                    from agents.run_control import cancel_run

                    cancel_run(stream_run_id, "client_disconnect")
                    break
                yield line
            from agents.run_control import is_cancelled as _is_cancelled

            finish_run(stream_run_id, "cancelled" if _is_cancelled(stream_run_id) else "complete")
            return

        if not message:
            finish_run(stream_run_id, "cancelled")
            yield _sse_data({"done": True, "ok": False, "error": "empty message"})
            return

        yield _sse_data({"type": "status", "phase": "supervisor", "message": "Running supervisor…"})
        recent_turns = []
        if chat_id:
            try:
                recent_turns = read_chat_log(chat_id)[-8:]
            except Exception:
                recent_turns = []
        thread = {}
        try:
            from agents.agent_state import load_state
            from memory.thread_context import infer_thread_from_turns, merge_thread, thread_from_state

            thread = merge_thread(thread_from_state(load_state(chat_id)), infer_thread_from_turns(recent_turns))
        except Exception:
            thread = {}
        decision = await asyncio.to_thread(
            compute_supervisor_decision,
            api_key,
            provider,
            message,
            coding_mode=bool(body.coding_mode),
            coding_project_context=coding_ctx,
            recent_turns=recent_turns,
            thread=thread,
        )
        agents_plan = decision.get("agents") or []
        if get_run_mode() == "plan":
            decision["run_agent"] = False
            agents_plan = []
        if custom_profile:
            agents_plan = filter_supervisor_plan(agents_plan, custom_profile.get("tools") or [])
            if not agents_plan:
                decision["run_agent"] = False
        goal = (decision.get("goal") or message).strip()
        is_task = bool(decision.get("run_agent")) and len(agents_plan) > 0
        route_labels = {
            "desktop": "desktop agent",
            "coding": "coding agent (sandbox)",
            "shell": "shell agent (host)",
            "finance": "finance agent (yfinance)",
            "google": "Google Workspace agent (Calendar / Gmail)",
        }
        if is_task:
            if len(agents_plan) == 1:
                ag = agents_plan[0].get("agent")
                sup_msg = f"Supervisor → running {route_labels.get(ag, ag)}"
            else:
                chain = " → ".join(route_labels.get(x.get("agent"), x.get("agent")) for x in agents_plan)
                sup_msg = f"Supervisor → plan: {chain}"
            yield _sse_data(
                {
                    "type": "status",
                    "phase": "supervisor_done",
                    "message": sup_msg,
                    "agent": agents_plan[0].get("agent") if agents_plan else None,
                    "agents": [
                        {"agent": x.get("agent"), "goal": (x.get("goal") or "")[:500]} for x in agents_plan
                    ],
                    "goal": goal[:500],
                    "reasoning": (decision.get("reasoning") or "")[:400],
                    "next_steps": (decision.get("next_steps") or "")[:800],
                }
            )
        else:
            yield _sse_data(
                {
                    "type": "status",
                    "phase": "supervisor_done",
                    "message": "Supervisor → chat (no agent run)",
                    "agent": None,
                    "agents": [],
                }
            )

        if not is_task:
            yield _sse_data({"type": "status", "phase": "context", "message": "Loading memory, tools, and history…"})
            hist, sys, tool_used = await asyncio.to_thread(_chat_history_and_system)
            yield _sse_data({"type": "status", "phase": "stream", "message": "Streaming reply…"})
            async for line in _stream_chat_reply(
                api_key,
                message,
                attachment_paths or None,
                hist,
                sys,
                tool_used,
                chat_id,
                provider_=provider,
                trace_user_message_=message,
                run_id_=stream_run_id,
            ):
                if await request.is_disconnected():
                    from agents.run_control import cancel_run

                    cancel_run(stream_run_id, "client_disconnect")
                    break
                yield line
            from agents.run_control import is_cancelled as _is_cancelled

            finish_run(stream_run_id, "cancelled" if _is_cancelled(stream_run_id) else "complete")
            return

        # Agent path: stream each step over SSE as it happens (WebSocket still gets full payload + screenshots)
        ag0 = agents_plan[0].get("agent") if agents_plan else None
        if len(agents_plan) <= 1:
            start_msg = f"Starting {route_labels.get(ag0, ag0)} — plan & steps will stream here"
        else:
            start_msg = f"Starting multi-agent plan ({len(agents_plan)} specialists) — steps stream below"
        yield _sse_data(
            {
                "type": "status",
                "phase": "agent_start",
                "message": start_msg,
                "agent": ag0,
                "agents": [x.get("agent") for x in agents_plan],
            }
        )
        step_queue: queue.Queue = queue.Queue()

        def on_step(step, thought, action, description, result, done, screenshot_base64=None):
            _persist_agent_step(step, thought, action, description, result, done)
            step_queue.put(
                {
                    "step": step,
                    "thought": thought or "",
                    "action": action or "",
                    "description": description or "",
                    "result": result,
                    "done": done,
                    "screenshot": screenshot_base64,
                }
            )

        initial_state = {
            "message": message,
            "attachment_paths": attachment_paths,
            "chat_id": chat_id,
            "api_key": api_key,
            "provider": provider,
            "on_step": on_step,
            "web_search_query": ws_q or None,
            "google_session_id": google_session_id,
            "coding_mode": bool(body.coding_mode),
            "coding_project_context": coding_ctx,
            "custom_agent_id": custom_profile["id"] if custom_profile else None,
            "custom_agent_system": build_agent_system_prompt(custom_profile, message) if custom_profile else None,
            "custom_agent_tools": list(custom_profile.get("tools") or []) if custom_profile else None,
            "resume_task_id": body.resume_task_id,
            "run_id": stream_run_id,
            "supervisor_decision": decision,
        }
        graph = _get_router_graph()
        stream_start = time.perf_counter()
        reply = ""
        route = "chat"
        tool_used = None
        file_edits: list | None = None
        graph_task: asyncio.Task | None = None

        stream_cancelled = False
        result: dict = {}
        try:
            from agents.run_control import is_cancelled, set_current_ids

            set_current_ids(run_id=stream_run_id, chat_id=chat_id or "")
            graph_task = asyncio.create_task(graph.ainvoke(initial_state))

            while True:
                if await request.is_disconnected():
                    from agents.run_control import cancel_run

                    cancel_run(stream_run_id, "client_disconnect")
                    stream_cancelled = True
                    graph_task.cancel()
                    try:
                        step_queue.put_nowait(_SENTINEL)
                    except Exception:
                        pass
                    try:
                        await graph_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    break
                step_wait = asyncio.create_task(asyncio.to_thread(step_queue.get))
                done_set, _ = await asyncio.wait(
                    {step_wait, graph_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if step_wait in done_set:
                    payload = step_wait.result()
                    if payload is _SENTINEL:
                        if not graph_task.done():
                            await graph_task
                        break
                    await _emit_agent_step(
                        payload["step"],
                        payload["thought"],
                        payload["action"],
                        payload["description"],
                        payload.get("result"),
                        payload.get("done", False),
                        payload.get("screenshot"),
                    )
                    yield _sse_data(_agent_step_for_sse(payload))
                    continue

                step_wait.cancel()
                try:
                    await step_wait
                except asyncio.CancelledError:
                    pass
                if graph_task.cancelled():
                    stream_cancelled = True
                    break
                exc = graph_task.exception()
                if exc is not None:
                    try:
                        step_queue.put_nowait(_SENTINEL)
                    except Exception:
                        pass
                    raise exc
                # Graph finished: all steps are already on the queue (sentinel is only queued in finally, after this try).
                while True:
                    try:
                        payload = step_queue.get_nowait()
                    except queue.Empty:
                        break
                    if payload is _SENTINEL:
                        continue
                    await _emit_agent_step(
                        payload["step"],
                        payload["thought"],
                        payload["action"],
                        payload["description"],
                        payload.get("result"),
                        payload.get("done", False),
                        payload.get("screenshot"),
                    )
                    yield _sse_data(_agent_step_for_sse(payload))

                break

            if is_cancelled(stream_run_id):
                stream_cancelled = True
            if (
                not stream_cancelled
                and graph_task is not None
                and graph_task.done()
                and not graph_task.cancelled()
            ):
                result = graph_task.result() or {}
                reply, file_edits = _strip_workspace_edits_from_reply(result.get("reply") or "No response.")
                route = result.get("route") or "chat"
                tool_used = result.get("tool_used")
                reply = _maybe_append_goal_critic(chat_id, message, reply, tool_used)
                if tool_used and chat_id:
                    append_chat_log("tool", json.dumps(tool_used), chat_id=chat_id)
                extra = _trace_extra(result)
                trace_log(
                    provider=provider,
                    route=route,
                    message=message,
                    reply=reply,
                    success=True,
                    duration_sec=time.perf_counter() - stream_start,
                    extra=extra,
                )
                schedule_post_turn_observability()
                _schedule_turn_writeback(chat_id, message, reply)
            elif stream_cancelled:
                trace_log(
                    provider=provider,
                    route="chat",
                    message=message,
                    reply=reply or "",
                    success=False,
                    error="cancelled",
                    duration_sec=time.perf_counter() - stream_start,
                    extra={"cancelled": True},
                )
        except asyncio.CancelledError:
            stream_cancelled = True
            try:
                from agents.run_control import cancel_run

                cancel_run(stream_run_id, "client_disconnect")
            except Exception:
                pass
            raise
        except Exception as e:
            trace_log(
                provider=provider,
                route="chat",
                message=message,
                reply="",
                success=False,
                error=str(e),
                duration_sec=time.perf_counter() - stream_start,
                extra={"failure_class": "infrastructure"},
            )
            finish_run(stream_run_id, "error")
            raise
        finally:
            try:
                step_queue.put_nowait(_SENTINEL)
            except Exception:
                pass

        if stream_cancelled:
            finish_run(stream_run_id, "cancelled")
            yield _sse_data({"done": True, "reply": reply or "Stopped.", "cancelled": True, "run_id": stream_run_id})
            return

        yield _sse_data({"type": "status", "phase": "done", "message": "Agent finished"})
        payload = {"done": True, "reply": reply}
        if file_edits:
            payload["file_edits"] = file_edits
        if tool_used:
            payload["tool_used"] = tool_used
        pending = result.get("pending_approvals") if isinstance(result, dict) else None
        if pending:
            payload["pending_approvals"] = pending
        if isinstance(result, dict) and result.get("run_id"):
            payload["run_id"] = result.get("run_id")
        elif stream_run_id:
            payload["run_id"] = stream_run_id
        if isinstance(result, dict) and result.get("task_id"):
            payload["task_id"] = result.get("task_id")
        finish_run(stream_run_id)
        yield f"data: {json.dumps(payload)}\n\n"

    async def _guarded_stream():
        try:
            async for line in event_stream():
                yield line
        except asyncio.CancelledError:
            try:
                from agents.run_control import cancel_run

                cancel_run(stream_run_id, "client_disconnect")
            except Exception:
                pass
            finish_run(stream_run_id, "cancelled")
            raise
        finally:
            try:
                from agents.run_control import get_run

                rec = get_run(stream_run_id)
                if rec and rec.get("status") == "running":
                    finish_run(stream_run_id, "cancelled")
            except Exception:
                pass

    return StreamingResponse(
        _guarded_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Chat log ---
@app.post("/chat/append")
async def api_append_chat_log(body: AppendChatLogRequest):
    role = (body.role or "").strip()
    if role not in ("user", "assistant", "tool"):
        raise HTTPException(status_code=400, detail="role must be user, assistant, or tool")
    content = body.content or ""
    if len(content) > 100_000:
        raise HTTPException(status_code=400, detail="content too long")
    if body.chat_id:
        _require_chat_id(body.chat_id)
    try:
        append_chat_log(role, content, chat_id=body.chat_id)
    except InvalidChatId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {}


@app.post("/chat/new")
async def api_new_chat():
    """Create a new empty chat and set it as current. Returns the new chat_id."""
    chat_id = create_new_chat()
    return {"chat_id": chat_id}


@app.get("/chat/list")
async def api_list_chats():
    return await asyncio.to_thread(list_chats)


@app.get("/chat/search")
async def api_search_chats(q: str = Query("", min_length=0), limit: int = 30):
    from memory.chat_search import search_chats

    return {"hits": await asyncio.to_thread(search_chats, q, limit=limit)}


@app.post("/chat/compact")
async def api_compact_chat(body: SetCurrentChatRequest):
    from memory.chat_search import extractive_compact

    _require_chat_id(body.chat_id)
    if not chat_exists(body.chat_id):
        raise HTTPException(status_code=404, detail="unknown chat")
    return {"ok": True, "summary": extractive_compact(body.chat_id)}


@app.get("/chat/handoff/{chat_id}")
async def api_handoff(chat_id: str):
    from memory.chat_search import handoff_markdown
    from agents.agent_state import load_state, structured_view

    extra = structured_view(load_state(chat_id))
    return {"ok": True, "markdown": handoff_markdown(chat_id, extra)}


class ForkChatRequest(BaseModel):
    chat_id: str
    message_index: int = 0
    label: str = ""


class MergeChatRequest(BaseModel):
    source_id: str
    target_id: str = ""


@app.post("/chat/fork")
async def api_fork_chat(body: ForkChatRequest):
    from memory.chat_log import fork_chat

    _require_chat_id(body.chat_id)
    result = fork_chat(body.chat_id, body.message_index, body.label)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "fork failed")
    return result


@app.get("/chat/branches/{chat_id}")
async def api_list_branches(chat_id: str):
    from memory.chat_log import list_branches

    _require_chat_id(chat_id)
    return {"branches": list_branches(chat_id)}


@app.get("/chat/meta/{chat_id}")
async def api_chat_meta(chat_id: str):
    from memory.chat_log import get_chat_meta

    _require_chat_id(chat_id)
    meta = get_chat_meta(chat_id)
    if not meta.get("ok"):
        raise HTTPException(status_code=404, detail="unknown chat")
    return meta


@app.post("/chat/merge")
async def api_merge_chat(body: MergeChatRequest):
    from memory.chat_log import merge_branch

    _require_chat_id(body.source_id)
    result = merge_branch(body.source_id, body.target_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "merge failed")
    return result


@app.get("/chat/recap/{chat_id}")
async def api_recap(chat_id: str):
    from memory.chat_search import recap_markdown
    from agents.agent_state import load_state, structured_view

    extra = structured_view(load_state(chat_id))
    return {"ok": True, "markdown": recap_markdown(chat_id, extra)}


class SlashRequest(BaseModel):
    command: str
    args: str = ""
    chat_id: str = ""


@app.post("/chat/slash")
async def api_slash(body: SlashRequest):
    from tools.slash_runtime import run_slash

    cid = (body.chat_id or "").strip()
    if cid:
        _require_chat_id(cid)
    result = run_slash(body.command, body.args, chat_id=cid)
    if result.get("kind") == "unknown" and not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error") or "unknown command")
    return result


@app.get("/workspace/slash-catalog")
async def api_slash_catalog():
    from tools.slash_commands import catalog

    return catalog()


class RewindChatRequest(BaseModel):
    chat_id: str
    keep_count: Optional[int] = None


@app.post("/chat/rewind")
async def api_rewind_chat(body: RewindChatRequest):
    from memory.chat_log import rewind_chat

    _require_chat_id(body.chat_id)
    result = rewind_chat(body.chat_id, keep_count=body.keep_count)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "rewind failed")
    return result


class ReactionRequest(BaseModel):
    chat_id: str = ""
    vote: str
    excerpt: str = ""
    message_id: str = ""


@app.post("/chat/reaction")
async def api_reaction(body: ReactionRequest):
    from memory.reactions import add_reaction

    return add_reaction(body.chat_id, body.vote, body.excerpt, body.message_id)


@app.get("/chat/reactions")
async def api_list_reactions(chat_id: str = ""):
    from memory.reactions import votes_for_chat

    return {"ok": True, "votes": votes_for_chat(chat_id)}


@app.get("/bookmarks")
async def api_list_bookmarks():
    from memory.bookmarks import list_bookmarks

    return {"bookmarks": list_bookmarks()}


class BookmarkAddRequest(BaseModel):
    chat_id: str = ""
    content: str
    title: str = ""


@app.post("/bookmarks")
async def api_add_bookmark(body: BookmarkAddRequest):
    from memory.bookmarks import add_bookmark

    return add_bookmark(body.chat_id, body.content, body.title)


@app.delete("/bookmarks/{bookmark_id}")
async def api_delete_bookmark(bookmark_id: str):
    from memory.bookmarks import remove_bookmark

    ok = remove_bookmark(bookmark_id)
    if not ok:
        raise HTTPException(status_code=404, detail="unknown bookmark")
    return {"ok": True}


@app.get("/observability/usage")
async def api_usage():
    traces = list_traces(limit=80)
    tin = sum(int(t.get("token_input") or 0) for t in traces)
    tout = sum(int(t.get("token_output") or 0) for t in traces)
    last = traces[-1] if traces else {}
    return {
        "window": "last_80_traces",
        "recent_runs": len(traces),
        "token_input": tin,
        "token_output": tout,
        "last": {
            "route": last.get("route"),
            "token_input": last.get("token_input"),
            "token_output": last.get("token_output"),
            "duration_sec": last.get("duration_sec"),
        },
    }


@app.post("/chat/set-current")
async def api_set_current_chat(body: SetCurrentChatRequest):
    try:
        set_current_chat(body.chat_id)
    except InvalidChatId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {}


@app.get("/chat/current-id")
async def api_get_current_chat_id():
    return {"chat_id": get_current_chat_id()}


@app.get("/chat/read/{chat_id}")
async def api_read_chat_log(chat_id: str):
    try:
        return await asyncio.to_thread(read_chat_log, chat_id)
    except InvalidChatId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/chat/{chat_id}")
async def api_delete_chat(chat_id: str):
    """Delete a chat by id. Returns ok and deleted=true if the chat was removed."""
    try:
        deleted = delete_chat(chat_id)
    except InvalidChatId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="unknown chat")
    return {"ok": True, "deleted": deleted}


# --- Memory: vector retrieval and ingest ---
class IngestChatRequest(BaseModel):
    chat_id: str


class UserIdentity(BaseModel):
    name: str | None = None
    pronouns: str | None = None
    languages: str | None = None


class UserDemographics(BaseModel):
    age_range: str | None = None
    gender: str | None = None
    timezone: str | None = None


class UserPersonality(BaseModel):
    communication: str | None = None
    learning_style: str | None = None
    risk_tolerance: str | None = None


class UserPreferences(BaseModel):
    tools_stack: str | None = None
    editor_environment: str | None = None
    code_style: str | None = None
    docs_comments: str | None = None


class UserGoals(BaseModel):
    current_projects: str | None = None
    standing_goals: str | None = None


class UserBoundaries(BaseModel):
    topics_avoid: str | None = None
    accessibility_needs: str | None = None


class UserProfilePayload(BaseModel):
    identity: UserIdentity
    demographics: UserDemographics
    personality: UserPersonality
    preferences: UserPreferences
    goals: UserGoals
    boundaries: UserBoundaries
    appendix_notes: list[str] = Field(default_factory=list)


@app.post("/memory/ingest")
async def api_memory_ingest(body: IngestChatRequest):
    """Ingest a chat's history into the vector store for retrieval."""
    from observability.rate_limit import allow as rate_allow

    if not rate_allow("memory.ingest", limit=4, window_sec=20.0):
        raise HTTPException(status_code=429, detail="Memory ingest is rate-limited.")
    _require_chat_id(body.chat_id)
    if not chat_exists(body.chat_id):
        raise HTTPException(status_code=404, detail="unknown chat")
    try:
        api_key = get_openai_api_key()
    except Exception:
        try:
            api_key = get_llm_api_key()
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    store = get_memory_store()
    n = ingest_chat(store, api_key, body.chat_id)
    return {"ok": True, "chunks_added": n, "store_size": len(store)}


@app.get("/memory/status")
async def api_memory_status():
    """Episodic store size, fact count, and context token budgets."""
    from config import get_context_budgets
    from memory.facts import fact_count

    store = get_memory_store()
    return {
        "episodic_chunks": len(store),
        "facts": fact_count(),
        "budgets": get_context_budgets(),
        "persistent": True,
        "hybrid_search": True,
        "writeback": True,
    }


@app.get("/memory/user-profile")
async def api_get_user_profile():
    """Structured profile (JSON on disk under `memory/user_profile.json`)."""
    return read_user_profile()


@app.put("/memory/user-profile")
async def api_put_user_profile(body: UserProfilePayload):
    """Replace profile on disk (validated shape)."""
    write_user_profile(body.model_dump(mode="json"))
    return {"ok": True}


# --- Storage ---
@app.get("/storage/chats-path")
async def api_get_chats_storage_path():
    return {"path": get_chats_storage_path()}


@app.post("/storage/chats-path")
async def api_set_chats_storage_path(body: SetStoragePathRequest):
    try:
        resolved = set_chats_storage_path(body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "path": resolved}


# --- Settings: LLM model / provider ---
@app.get("/settings/model")
async def api_get_model():
    """Return current LLM provider and local model id if any."""
    return {
        "provider": get_llm_provider(),
        "local_model_id": get_local_model_id() or None,
    }


@app.post("/settings/model")
async def api_set_model(body: SetModelRequest):
    """Set LLM provider to openai, xai, or local."""
    p = (body.provider or "").strip().lower()
    if p.startswith("local:"):
        body.local_model_id = p.split(":", 1)[1]
        p = "local"
    set_llm_provider(p)
    if p == "local" and (body.local_model_id or "").strip():
        mid = body.local_model_id.strip()
        set_local_model_id(mid)
        from agents.local_models import is_installed, load_model

        if is_installed(mid):
            await asyncio.to_thread(load_model, mid)
    return {
        "provider": get_llm_provider(),
        "local_model_id": get_local_model_id() or None,
    }


@app.get("/settings/hardware")
async def api_hardware():
    from agents.local_models import public_status

    return await asyncio.to_thread(public_status)


@app.get("/settings/local-models")
async def api_local_models():
    from agents.local_models import public_status

    return await asyncio.to_thread(public_status)


@app.get("/settings/local-models/status")
async def api_local_models_status():
    from agents.local_models import job_status, loaded_model_id

    return {**job_status(), "loaded_model_id": loaded_model_id()}


@app.post("/settings/local-models/download")
async def api_local_models_download(body: LocalModelRequest):
    from agents.local_models import download_model

    return await asyncio.to_thread(download_model, body.model_id)


@app.post("/settings/local-models/load")
async def api_local_models_load(body: LocalModelRequest):
    from agents.local_models import load_model
    from agents.models.local_client import refresh_chat_model_label

    result = await asyncio.to_thread(load_model, body.model_id)
    set_local_model_id(body.model_id)
    set_llm_provider("local")
    refresh_chat_model_label()
    return result


@app.get("/settings/runtime")
async def api_get_runtime():
    hw = {}
    try:
        from agents.hardware import hardware_snapshot

        hw = hardware_snapshot()
    except Exception:
        hw = {}
    return {
        "provider": get_llm_provider(),
        "autonomy": get_autonomy_level(),
        "desktop_armed": is_desktop_armed(),
        "packaged": is_packaged(),
        "keys": api_keys_status(),
        "hardware": hw,
        "run_mode": get_run_mode(),
        "spend": get_spend_limits(),
        "quiet_hours": get_quiet_hours(),
    }


class SetRunModeRequest(BaseModel):
    run_mode: str


class SetSpendRequest(BaseModel):
    max_tokens_per_run: Optional[int] = None
    warn_tokens: Optional[int] = None


class SetQuietHoursRequest(BaseModel):
    enabled: Optional[bool] = None
    start: Optional[str] = None
    end: Optional[str] = None
    timezone: Optional[str] = None


class FactRequest(BaseModel):
    text: str
    key: Optional[str] = None


class IdentityRequest(BaseModel):
    soul: Optional[str] = None
    user: Optional[str] = None
    memory: Optional[str] = None


class TaskPatchRequest(BaseModel):
    status: Optional[str] = None
    next_action: Optional[str] = None
    owner: Optional[str] = None


class SteerRequest(BaseModel):
    note: str = ""


class CheckpointRestoreRequest(BaseModel):
    checkpoint_id: str


@app.post("/settings/autonomy")
async def api_set_autonomy(body: SetAutonomyRequest):
    set_autonomy_level(body.autonomy)
    return {"autonomy": get_autonomy_level()}


@app.post("/settings/run-mode")
async def api_set_run_mode(body: SetRunModeRequest):
    return {"run_mode": set_run_mode(body.run_mode)}


@app.post("/settings/spend")
async def api_set_spend(body: SetSpendRequest):
    return set_spend_limits(
        max_tokens_per_run=body.max_tokens_per_run,
        warn_tokens=body.warn_tokens,
    )


@app.post("/settings/quiet-hours")
async def api_set_quiet_hours(body: SetQuietHoursRequest):
    return set_quiet_hours(
        enabled=body.enabled,
        start=body.start,
        end=body.end,
        timezone=body.timezone,
    )


@app.post("/settings/desktop-armed")
async def api_set_desktop_armed(body: SetDesktopArmedRequest):
    set_desktop_armed(bool(body.armed))
    return {"desktop_armed": is_desktop_armed()}


@app.get("/settings/keys-status")
async def api_keys_status_ep():
    return api_keys_status()


@app.post("/settings/keys")
async def api_set_keys(body: SetApiKeysRequest):
    write_api_keys(openai_key=body.openai_api_key, xai_key=body.xai_api_key)
    return api_keys_status()


@app.get("/workspace/status")
async def api_workspace_status():
    from tools.workspace_io import status as ws_status

    return ws_status()


@app.post("/workspace/link")
async def api_workspace_link(body: WorkspaceLinkRequest):
    from tools.workspace_io import link_workspace

    return link_workspace(body.path)


@app.post("/workspace/unlink")
async def api_workspace_unlink():
    from tools.workspace_io import unlink_workspace

    unlink_workspace()
    return {"ok": True}


@app.post("/workspace/snapshot")
async def api_workspace_snapshot():
    from tools.workspace_io import snapshot as ws_snapshot

    try:
        return await asyncio.to_thread(ws_snapshot)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/workspace/file")
async def api_workspace_read(rel_path: str = Query(...)):
    from tools.workspace_io import read_file

    try:
        return read_file(rel_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.put("/workspace/file")
async def api_workspace_write(body: WorkspaceWriteRequest):
    from tools.workspace_io import write_file

    try:
        return write_file(body.rel_path, body.content)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/workspace/list")
async def api_workspace_list():
    from tools.workspace_io import list_tree_paths

    try:
        paths = await asyncio.to_thread(list_tree_paths)
        return {"ok": True, "paths": paths}
    except Exception as e:
        return {"ok": False, "error": str(e), "paths": []}


@app.get("/workspace/tree-stamp")
async def api_workspace_tree_stamp():
    from tools.workspace_io import tree_stamp

    try:
        return await asyncio.to_thread(tree_stamp)
    except Exception as e:
        return {"ok": False, "error": str(e), "stamp": "", "count": 0}


@app.post("/workspace/run")
async def api_workspace_run(body: WorkspaceRunRequest):
    from tools.workspace_run import run_workspace_file

    try:
        return await asyncio.to_thread(run_workspace_file, body.rel_path, body.timeout_sec)
    except Exception as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(e),
            "runtime": None,
            "display": "",
        }


@app.get("/agent/pending")
async def api_agent_pending(chat_id: Optional[str] = None):
    from agents.hitl import list_pending

    return {"pending": list_pending(chat_id)}


@app.post("/agent/approve")
async def api_agent_approve(body: AgentApproveRequest):
    from agents.hitl import resolve_approval

    return resolve_approval(body.approval_id, bool(body.approve), chat_id=body.chat_id)


@app.get("/tasks")
async def api_list_tasks(chat_id: Optional[str] = None, open_only: bool = False):
    from agents.tasks import list_tasks

    return {"tasks": list_tasks(chat_id=chat_id, open_only=open_only)}


@app.get("/tasks/{task_id}")
async def api_get_task(task_id: str):
    from agents.tasks import get_task

    task = get_task(task_id)
    if not task:
        return JSONResponse({"ok": False, "error": "unknown_task"}, status_code=404)
    return task


@app.patch("/tasks/{task_id}")
async def api_patch_task(task_id: str, body: TaskPatchRequest):
    from agents.tasks import update_task

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    task = update_task(task_id, **fields)
    if not task:
        return JSONResponse({"ok": False, "error": "unknown_task"}, status_code=404)
    return task


@app.post("/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str):
    from agents.tasks import get_task, update_task
    from agents.run_control import cancel_run

    task = get_task(task_id)
    if not task:
        return JSONResponse({"ok": False, "error": "unknown_task"}, status_code=404)
    if task.get("run_id"):
        cancel_run(task["run_id"], "task_cancel")
    return update_task(task_id, status="cancelled") or task


@app.get("/agent/runs")
async def api_list_runs(chat_id: Optional[str] = None):
    from agents.run_control import list_active

    return {"runs": list_active(chat_id)}


@app.post("/agent/runs/{run_id}/stop")
async def api_stop_run(run_id: str):
    from agents.run_control import cancel_run

    rec = cancel_run(run_id, "user_stop")
    if not rec:
        return JSONResponse({"ok": False, "error": "unknown_run"}, status_code=404)
    return rec


@app.post("/agent/runs/{run_id}/steer")
async def api_steer_run(run_id: str, body: SteerRequest):
    from agents.run_control import push_steer

    rec = push_steer(run_id, body.note)
    if not rec:
        return JSONResponse({"ok": False, "error": "unknown_run"}, status_code=404)
    return rec


@app.get("/agent/checkpoints")
async def api_list_checkpoints(run_id: Optional[str] = None):
    from agents.run_control import list_checkpoints

    return {"checkpoints": list_checkpoints(run_id)}


@app.post("/agent/checkpoints/restore")
async def api_restore_checkpoint(body: CheckpointRestoreRequest):
    from agents.run_control import restore_checkpoint

    return restore_checkpoint(body.checkpoint_id)


@app.get("/memory/facts")
async def api_list_facts():
    from memory.facts import list_facts

    return {"facts": list_facts()}


@app.post("/memory/facts")
async def api_add_fact(body: FactRequest):
    from memory.facts import add_fact

    item = add_fact(body.text, key=body.key or "", source="user")
    return {"ok": bool(item), "fact": item}


@app.delete("/memory/facts/{fact_id}")
async def api_delete_fact(fact_id: str):
    from memory.facts import delete_fact

    return {"ok": delete_fact(fact_id)}


@app.get("/memory/identity")
async def api_get_identity():
    from memory.identity import read_identity

    return read_identity()


@app.put("/memory/identity")
async def api_put_identity(body: IdentityRequest):
    from memory.identity import write_identity

    return write_identity(soul=body.soul, user=body.user, memory=body.memory)


# --- Google OAuth (multi-user scaffold) ---
@app.get("/auth/google/status")
async def api_google_auth_status(request: Request):
    sid = _google_session_cookie(request)
    info = google_status_by_session(sid)
    info["redirect_uri"] = oauth_redirect_uri()
    info["client_id_hint"] = oauth_client_id_hint()
    info["javascript_origin_hint"] = oauth_suggested_javascript_origin()
    if not info.get("configured"):
        info["missing_fields"] = oauth_missing_config_fields()
    return info


@app.get("/auth/google/login")
async def api_google_auth_login(next_path: str = Query("/", alias="next")):
    try:
        auth_url = create_login_url(next_path=next_path)
    except Exception as e:
        return RedirectResponse(url=callback_error_redirect(str(e)))
    return RedirectResponse(url=auth_url)


@app.get("/auth/google/callback")
async def api_google_auth_callback(code: str | None = None, state: str | None = None):
    if not code or not state:
        return RedirectResponse(url=callback_error_redirect("Missing OAuth code/state"))
    try:
        sid, next_path = await asyncio.to_thread(
            exchange_code_and_create_session, code=code, state=state
        )
        redirect_url = callback_success_redirect(next_path)
        resp = RedirectResponse(url=redirect_url)
        resp.set_cookie(
            _GOOGLE_SID_COOKIE,
            sid,
            httponly=True,
            samesite="lax",
            secure=cookie_secure(),
            max_age=30 * 24 * 60 * 60,
            path="/",
        )
        return resp
    except Exception as e:
        return RedirectResponse(url=callback_error_redirect(str(e)))


@app.post("/auth/google/logout")
async def api_google_auth_logout(request: Request):
    sid = _google_session_cookie(request)
    logout_session(sid)
    out = JSONResponse({"ok": True})
    _clear_google_sid_cookies(out)
    return out


@app.post("/auth/google/disconnect")
async def api_google_auth_disconnect(request: Request):
    sid = _google_session_cookie(request)
    disconnect_session(sid)
    out = JSONResponse({"ok": True})
    _clear_google_sid_cookies(out)
    return out


@app.get("/integrations/gmail/profile")
async def api_gmail_profile(request: Request):
    """Gmail API profile for the signed-in Google user (uses OAuth token from sign-in)."""
    sid = _google_session_cookie(request)
    token, err = get_valid_access_token_for_session(sid)
    if not token:
        return JSONResponse({"ok": False, "error": err or "Unauthorized"}, status_code=401)
    try:
        profile = await asyncio.to_thread(fetch_gmail_profile, token)
        return {
            "ok": True,
            "emailAddress": profile.get("emailAddress"),
            "messagesTotal": profile.get("messagesTotal"),
            "threadsTotal": profile.get("threadsTotal"),
        }
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = (e.response.text or "")[:400]
        except Exception:
            pass
        return JSONResponse(
            {
                "ok": False,
                "error": f"Gmail API HTTP {e.response.status_code}",
                "detail": detail,
            },
            status_code=502,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


# --- Observability: traces, evals, optimization (per-model) ---
@app.get("/observability/traces")
async def api_get_traces(limit: int = 500):
    """List recent trace logs (success rates, tokens, errors per run)."""
    return {"traces": list_traces(limit=limit)}


@app.get("/observability/spans")
async def api_get_spans(limit: int = 200, trace_id: Optional[str] = None):
    """Hierarchical turn spans (retrieval, tools, llm, specialists)."""
    from observability.spans import list_spans

    return {"spans": list_spans(limit=max(1, min(limit, 2000)), trace_id=trace_id)}


@app.get("/observability/metrics")
async def api_get_metrics():
    """In-process counters/histograms plus last flushed snapshot."""
    from observability.metrics import load_latest, snapshot

    return {"live": snapshot(), "persisted": load_latest()}


@app.get("/observability/logs")
async def api_get_struct_logs(limit: int = 200):
    """Tail structured JSON application logs."""
    from observability.struct_log import list_recent_logs

    return {"logs": list_recent_logs(limit=max(1, min(limit, 2000)))}


@app.get("/observability/actions")
async def api_get_agent_actions(limit: int = 200, trace_id: Optional[str] = None):
    """Persisted specialist steps (desktop clicks, coding, shell)."""
    from observability.actions import list_actions

    return {"actions": list_actions(limit=max(1, min(limit, 2000)), trace_id=trace_id)}


@app.post("/observability/feedback-assess")
async def api_feedback_assess(body: FeedbackAssessRequest):
    """
    Run user-triggered quality review for a chat whose last log line is a complaint
    (same logic as POST /chat/send-message when the message matches complaint heuristics).
    """
    provider = get_llm_provider()
    result = await asyncio.to_thread(run_feedback_assessment, body.chat_id, provider)
    return {
        "ok": result.get("ok"),
        "reply": format_feedback_assessment_markdown(result),
        "meta": {
            "selected_provider": result.get("selected_provider"),
            "alternate_provider": result.get("alternate_provider"),
            "assessor_provider": result.get("assessor_provider"),
        },
        "error": result.get("error"),
    }


@app.post("/observability/evals/generate")
async def api_generate_evals(num_traces: int = 30, num_cases: int = 5):
    """Generate multi-turn eval cases from recent trace logs (LLM-based)."""
    from observability.rate_limit import allow as rate_allow

    if not rate_allow("obs.evals.generate", limit=2, window_sec=30.0):
        raise HTTPException(status_code=429, detail="Eval generate is rate-limited.")
    cases = await asyncio.to_thread(generate_evals_from_logs, num_traces=num_traces, num_cases=num_cases)
    return {"generated": len(cases), "cases": [c.to_dict() for c in cases]}


@app.get("/observability/evals/cases")
async def api_get_eval_cases(limit: int = 100):
    return {"cases": [c.to_dict() for c in load_eval_cases(limit=limit)]}


@app.post("/observability/evals/run")
async def api_run_evals(case_limit: int = 20):
    """Run eval cases for all models (openai, xai); record pass@k."""
    from observability.rate_limit import allow as rate_allow

    if not rate_allow("obs.evals.run", limit=2, window_sec=60.0):
        raise HTTPException(status_code=429, detail="Eval run is rate-limited.")
    runs = await asyncio.to_thread(run_evals_for_all_models, case_limit=case_limit)
    by_provider = pass_at_k([r.to_dict() for r in runs])
    return {"runs": len(runs), "pass_at_1": by_provider}


@app.get("/observability/evals/runs")
async def api_get_eval_runs(limit: int = 200):
    return {"runs": load_eval_runs(limit=limit)}


@app.get("/observability/optimization")
async def api_get_optimization():
    """Latest optimization stats (success rates, eval pass, suggestions)."""
    stats = get_latest_optimization_stats()
    return stats if stats is not None else {"note": "Run POST /observability/optimization/run first"}


@app.post("/observability/optimization/run")
async def api_run_optimization():
    """Aggregate traces + eval runs, compute per-model stats and suggestions."""
    from observability.rate_limit import allow as rate_allow

    if not rate_allow("obs.optimize", limit=2, window_sec=30.0):
        raise HTTPException(status_code=429, detail="Optimization is rate-limited.")
    return await asyncio.to_thread(run_optimization_step)


@app.post("/observability/human-eval")
async def api_human_eval(max_problems: int = 5):
    """Run HumanEval benchmark for each model (optional; needs datasets)."""
    from observability.rate_limit import allow as rate_allow

    if not rate_allow("obs.human_eval", limit=1, window_sec=60.0):
        raise HTTPException(status_code=429, detail="HumanEval is rate-limited.")
    return await asyncio.to_thread(run_human_eval_benchmark, max_problems=max_problems)


# --- File upload for attachments (web: frontend sends files as multipart) ---
@app.post("/chat/send-message-with-files")
async def send_message_with_files(
    request: Request,
    message: str = Form(""),
    chat_id: Optional[str] = Form(None),
    web_search_query: Optional[str] = Form(None),
    coding_mode: bool = Form(False),
    coding_project_snapshot: Optional[str] = Form(None),
    custom_agent_id: Optional[str] = Form(None),
    resume_task_id: Optional[str] = Form(None),
    files: list[UploadFile] = File(default=[]),
):
    """Accept multipart form: message + files. Saves files to temp and calls send_message."""
    import os
    import re
    import uuid

    paths = []
    _UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        for f in files[:8]:
            if not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext or ""):
                ext = ".bin"
            path = _UPLOAD_ROOT / f"{uuid.uuid4().hex}{ext}"
            data = await f.read()
            if len(data) > 10 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="attachment too large")
            path.write_bytes(data)
            paths.append(str(path))
        body = SendMessageRequest(
            message=message.strip(),
            attachment_paths=paths if paths else None,
            chat_id=chat_id,
            web_search_query=(web_search_query or "").strip() or None,
            coding_mode=bool(coding_mode),
            coding_project_snapshot=(coding_project_snapshot or "").strip() or None,
            custom_agent_id=(custom_agent_id or "").strip() or None,
            resume_task_id=(resume_task_id or "").strip() or None,
        )
        result = await send_message(body, request)
        return result
    finally:
        for p in paths:
            try:
                os.unlink(p)
            except Exception:
                pass


# --- WebSocket for desktop-agent-step ---
@app.websocket("/ws/agent-steps")
async def websocket_agent_steps(ws: WebSocket):
    from auth.local_token import ws_has_valid_token

    if not ws_has_valid_token(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    async with _ws_lock:
        _ws_connections.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            if ws in _ws_connections:
                _ws_connections.remove(ws)


# --- Tools ---
@app.get("/tools/weather")
async def api_tools_weather(location: str = Query(..., description="City or place name")):
    """Get current weather for a location (Open-Meteo, no API key)."""
    result = await asyncio.to_thread(get_weather, location)
    return {"location": location, "result": result}


@app.get("/tools/grep")
async def api_tools_grep(
    q: str = Query(..., description="Search pattern (literal by default; set regex=1 for regex)"),
    root: Optional[str] = Query(
        None,
        description="Directory to search (defaults to grep.default_root in jarvis-config.yaml or jarvis-grep-root.txt)",
    ),
    limit: int = Query(100, ge=1, le=5000),
    regex: bool = Query(False, description="If true, pattern is a regex (ripgrep / Python re)"),
    case_sensitive: bool = Query(False),
):
    """
    Search files under a directory like Cursor-style ripgrep (uses `rg` when on PATH, else Python scan).
    Skips common noise dirs (.git, node_modules, __pycache__, …). No vector index.
    """
    raw_root = (root or "").strip()
    if raw_root:
        allowed = get_grep_root()
        try:
            base = Path(raw_root).expanduser().resolve()
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        allowed_ok = False
        if allowed is not None:
            try:
                base.relative_to(allowed.resolve())
                allowed_ok = True
            except ValueError:
                allowed_ok = False
        if not allowed_ok:
            ws = ""
            try:
                from config import get_workspace_root

                ws = (get_workspace_root() or "").strip()
            except Exception:
                ws = ""
            if ws:
                try:
                    wr = Path(ws).expanduser().resolve()
                    base.relative_to(wr)
                except (OSError, ValueError) as exc:
                    raise HTTPException(status_code=403, detail="grep root is not allowed") from exc
            else:
                raise HTTPException(status_code=403, detail="grep root is not allowed")
    else:
        gr = get_grep_root()
        if gr is None:
            return {
                "ok": False,
                "error": "No search root: pass root= or set grep.default_root in jarvis-config.yaml or create jarvis-grep-root.txt with a directory path.",
                "matches": [],
            }
        base = gr
    return await asyncio.to_thread(
        grep_files,
        base,
        q.strip(),
        max_results=limit,
        fixed_string=not regex,
        ignore_case=not case_sensitive,
    )


@app.post("/tools/web-search")
async def api_tools_web_search(body: WebSearchRequest):
    """DuckDuckGo text search (no API key). Same engine as chat / agent web_search tool."""
    from tools.web_search import search_web

    q = (body.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="empty query")
    text = await asyncio.to_thread(search_web, q)
    return {"ok": True, "query": q, "results_text": text}


@app.post("/tools/python-sandbox")
async def api_tools_python_sandbox(body: PythonSandboxRequest):
    """
    Execute Python in a sandboxed child process (timeout, restricted imports/builtins).
    For agents/models: prefer this over exec on the server process.
    """
    from observability.rate_limit import allow as rate_allow

    if not rate_allow("tools.sandbox", limit=8, window_sec=15.0):
        raise HTTPException(status_code=429, detail="Sandbox is rate-limited. Try again shortly.")
    result = await asyncio.to_thread(run_sandboxed_python, body.code, body.timeout_sec)
    if isinstance(result, dict):
        return redact_sandbox_result_dict(result)
    return result


@app.post("/tools/shell")
async def api_tools_shell(body: ShellRunRequest):
    """
    Run a single shell command on the host (same backend as the shell agent).
    Disabled only if ADA_ENABLE_SHELL=0 or ADA_DISABLE_SHELL=1. Dangerous — do not expose publicly.
    """
    if not is_shell_enabled():
        raise HTTPException(
            status_code=403,
            detail="Shell disabled on server (remove ADA_DISABLE_SHELL or set ADA_ENABLE_SHELL=1).",
        )
    if in_quiet_hours():
        raise HTTPException(status_code=403, detail="Quiet hours are enabled; shell is paused.")
    from observability.rate_limit import allow as rate_allow

    if not rate_allow("tools.shell", limit=6, window_sec=15.0):
        raise HTTPException(status_code=429, detail="Shell is rate-limited. Try again shortly.")
    from agents.hitl import maybe_gate_shell
    from agents.run_control import finish_run, start_run

    rec = start_run(chat_id="", task_id="")
    rid = rec.get("run_id")
    try:
        result = await asyncio.to_thread(
            maybe_gate_shell,
            body.command,
            execute=lambda: run_shell_command(body.command, body.timeout_sec, run_id=rid),
        )
    finally:
        from agents.run_control import is_cancelled

        finish_run(rid, "cancelled" if is_cancelled(rid) else "complete")
    return result


@app.get("/health")
async def health():
    hw: dict = {}
    try:
        from agents.hardware import hardware_snapshot

        raw = hardware_snapshot()
        hw = {
            "os": raw.get("os"),
            "arch": raw.get("arch"),
            "has_gpu": raw.get("has_gpu"),
            "has_npu": raw.get("has_npu"),
            "system_ram_gb": raw.get("system_ram_gb"),
            "usable_memory_gb": raw.get("usable_memory_gb"),
            "recommended_runtime": raw.get("recommended_runtime"),
        }
    except Exception:
        pass
    return {"status": "ok", "packaged": is_packaged(), "hardware": hw}


if __name__ == "__main__":
    import os

    import uvicorn

    # So imports (config, agents, …) work even if you run from repo root
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("UVICORN_RELOAD", "0").lower() in ("1", "true", "yes")
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=reload)
