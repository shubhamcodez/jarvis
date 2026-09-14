"""FastAPI backend: chat, send_message (classify + agent/chat), chat log, storage, WebSocket for agent steps."""
from __future__ import annotations

import asyncio
import queue
import sys
import time
import warnings
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
from fastapi import FastAPI, File, Form, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
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
    get_openai_api_key,
    is_desktop_armed,
    is_packaged,
    set_autonomy_level,
    set_desktop_armed,
    set_llm_provider,
    write_api_keys,
)
from agents.models import get_llm_client
from agents.supervisor import compute_supervisor_decision
from memory import get_memory_store, ingest_chat, run_retrieval_pipeline
from memory.user_profile_io import read_user_profile, write_user_profile
from memory.chat_log import (
    append_chat_log,
    create_new_chat,
    delete_chat,
    get_current_chat_id,
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
from tools.workspace_file_edits import extract_ada_file_edits
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


app = FastAPI(title="Ada API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:1420",
        "http://localhost:1430",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connections for desktop-agent-step broadcasts
_ws_connections: list[WebSocket] = []
_SENTINEL = object()


def _sse_data(obj: dict) -> str:
    """One SSE event line (JSON payload)."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _strip_workspace_edits_from_reply(reply: str) -> tuple[str, list | None]:
    """Remove ```ada-file:...``` blocks from visible reply; return pending edits for UI."""
    clean, edits = extract_ada_file_edits(reply or "")
    return clean, edits if edits else None


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
    if screenshot is not None:
        payload["screenshot"] = screenshot
    for ws in _ws_connections[:]:
        try:
            await ws.send_json(payload)
        except Exception:
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


class AppendChatLogRequest(BaseModel):
    role: str
    content: str


class SetCurrentChatRequest(BaseModel):
    chat_id: str


class FeedbackAssessRequest(BaseModel):
    chat_id: str


class SetStoragePathRequest(BaseModel):
    path: str


class SetModelRequest(BaseModel):
    provider: str  # "openai" or "xai"


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


class AgentApproveRequest(BaseModel):
    approval_id: str
    approve: bool = True


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
    try:
        from tools.runner import run_tools_for_turn

        tool_sys, tool_used = await asyncio.to_thread(
            run_tools_for_turn, msg, None, ws_q
        )
        system_content = (tool_sys or "").strip() or None
        reply = await asyncio.to_thread(
            client.chat, api_key, msg, paths if paths else None, None, system_content
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
    except Exception as e:
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
    out: dict = {"reply": reply}
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
    attachment_paths = body.attachment_paths or []
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
            return {"reply": reply, "tool_used": None}
        except Exception as e:
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
        step_queue.put({
            "step": step, "thought": thought or "", "action": action or "",
            "description": description or "", "result": result, "done": done,
            "screenshot": screenshot_base64,
        })

    async def drain_steps():
        loop = asyncio.get_event_loop()
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
    }
    graph = _get_router_graph()
    drain_task = asyncio.create_task(drain_steps())
    start = time.perf_counter()
    file_edits = None
    try:
        result = await graph.ainvoke(initial_state)
        reply, file_edits = _strip_workspace_edits_from_reply(result.get("reply") or "No response.")
        route = result.get("route") or "chat"
        tool_used = result.get("tool_used")
        if tool_used and chat_id:
            set_current_chat(chat_id)
            append_chat_log("tool", json.dumps(tool_used))
        extra = _trace_extra(result)
        extra["step_count"] = extra.get("step_count")
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
    except Exception as e:
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
        step_queue.put(_SENTINEL)
        await drain_task
    out = {"reply": reply}
    if file_edits:
        out["file_edits"] = file_edits
    if result.get("tool_used"):
        out["tool_used"] = result["tool_used"]
    if result.get("pending_approvals"):
        out["pending_approvals"] = result["pending_approvals"]
    if result.get("run_id"):
        out["run_id"] = result["run_id"]
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
    attachment_paths = body.attachment_paths or []
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
    ):
        """Run sync chat_stream in executor and yield SSE as chunks arrive. Optional tool_used for final event."""
        chunk_queue = queue.Queue()
        loop = asyncio.get_event_loop()
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
        while True:
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
            yield f"data: {json.dumps({'delta': chunk})}\n\n"
        reply, file_edits = _strip_workspace_edits_from_reply("".join(full))
        if provider_ is not None and trace_user_message_ is not None:
            trace_log(
                provider=provider_,
                route="chat",
                message=trace_user_message_,
                reply=reply,
                success=True,
                duration_sec=time.perf_counter() - t0,
            )
            schedule_post_turn_observability()
        payload = {"done": True, "reply": reply}
        if file_edits:
            payload["file_edits"] = file_edits
        if tool_used_:
            payload["tool_used"] = tool_used_
            if chat_id_:
                set_current_chat(chat_id_)
                append_chat_log("tool", json.dumps(tool_used_))
        yield f"data: {json.dumps(payload)}\n\n"

    def _chat_history_and_system():
        """Every conversation: (1) load full history, (2) run tool calls (e.g. weather), (3) build system. Returns (hist, sys_content, tool_used)."""
        # 1) Conversation history goes into every turn (same for all messages in this chat)
        _lim = get_chat_history_limit()
        hist = read_chat_log(chat_id)[-_lim:] if chat_id else None
        sys_content = None
        try:
            from config import get_openai_api_key
            store = get_memory_store()
            if len(store) > 0:
                sys_content, _ = run_retrieval_pipeline(
                    store, get_openai_api_key(),
                    current_message=message,
                    recent_turns=hist or [],
                    task_state={"route": "chat"},
                    top_k=12,
                    include_raw_top_n=4,
                    max_memory_raw_chars=4500,
                )
                sys_content = (sys_content or "").strip() or None
        except Exception:
            pass
        # 2) Tool calls: run applicable tools (weather app, etc.) using conversation context; then inject results
        from tools.runner import run_tools_for_turn
        tool_system, tool_used = run_tools_for_turn(
            message or "", recent_turns=hist or [], web_search_query=ws_q or None
        )
        if tool_system:
            sys_content = (tool_system + "\n\n" + (sys_content or "")) if sys_content else tool_system
        sys_final = (sys_content.strip() or None) if sys_content else None
        if (coding_ctx or "").strip():
            inj = (
                "\n\n## Project workspace (linked folder)\n"
                "The client sends a snapshot of the user's **currently open folder**. When they ask you to fix, implement, "
                "or refactor **project** code, output each updated file as a markdown code fence whose **first line** is "
                "exactly `ada-file:relative/path/from/root.ext` (then a newline), then the **complete** new file contents "
                "(full file, not a patch), then a closing line ` ``` ` (three backticks) alone. "
                "Use forward slashes; one fence per file. The UI shows a diff and applies changes on the user's machine. "
                "Other code fences are for examples only; only `ada-file:` openers become pending workspace edits."
            )
            sys_final = (sys_final + inj) if sys_final else inj.strip()
        return hist, sys_final, tool_used

    async def event_stream():
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
                yield f"data: {json.dumps({'done': True, 'reply': reply_md})}\n\n"
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
                yield f"data: {json.dumps({'done': True, 'reply': fallback})}\n\n"
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
            ):
                yield line
            return

        if not message:
            yield _sse_data({"type": "status", "phase": "context", "message": "Loading context…"})
            hist, sys, tool_used = await asyncio.to_thread(_chat_history_and_system)
            yield _sse_data({"type": "status", "phase": "stream", "message": "Streaming reply…"})
            async for line in _stream_chat_reply(
                api_key,
                "Hello.",
                None,
                hist,
                sys,
                tool_used,
                chat_id,
                provider_=provider,
                trace_user_message_=message,
            ):
                yield line
            return

        yield _sse_data({"type": "status", "phase": "supervisor", "message": "Running supervisor…"})
        decision = await asyncio.to_thread(
            compute_supervisor_decision,
            api_key,
            provider,
            message,
            coding_mode=bool(body.coding_mode),
            coding_project_context=coding_ctx,
        )
        agents_plan = decision.get("agents") or []
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
            ):
                yield line
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
        }
        graph = _get_router_graph()
        stream_start = time.perf_counter()
        reply = ""
        route = "chat"
        tool_used = None
        file_edits: list | None = None
        graph_task: asyncio.Task | None = None

        try:
            graph_task = asyncio.create_task(graph.ainvoke(initial_state))

            while True:
                step_wait = asyncio.create_task(asyncio.to_thread(step_queue.get))
                done_set, _ = await asyncio.wait(
                    {step_wait, graph_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if step_wait in done_set:
                    payload = step_wait.result()
                    if payload is _SENTINEL:
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

            assert graph_task is not None
            result = graph_task.result()
            reply, file_edits = _strip_workspace_edits_from_reply(result.get("reply") or "No response.")
            route = result.get("route") or "chat"
            tool_used = result.get("tool_used")
            if tool_used and chat_id:
                set_current_chat(chat_id)
                append_chat_log("tool", json.dumps(tool_used))
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
            raise
        finally:
            try:
                step_queue.put_nowait(_SENTINEL)
            except Exception:
                pass

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
        yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Chat log ---
@app.post("/chat/append")
async def api_append_chat_log(body: AppendChatLogRequest):
    append_chat_log(body.role, body.content)
    return {}


@app.post("/chat/new")
async def api_new_chat():
    """Create a new empty chat and set it as current. Returns the new chat_id."""
    chat_id = create_new_chat()
    return {"chat_id": chat_id}


@app.get("/chat/list")
async def api_list_chats():
    return list_chats()


@app.get("/chat/search")
async def api_search_chats(q: str = Query("", min_length=0), limit: int = 30):
    from memory.chat_search import search_chats

    return {"hits": search_chats(q, limit=limit)}


@app.post("/chat/compact")
async def api_compact_chat(body: SetCurrentChatRequest):
    from memory.chat_search import extractive_compact

    return {"ok": True, "summary": extractive_compact(body.chat_id)}


@app.get("/chat/handoff/{chat_id}")
async def api_handoff(chat_id: str):
    from memory.chat_search import handoff_markdown
    from agents.agent_state import load_state, structured_view

    extra = structured_view(load_state(chat_id))
    return {"ok": True, "markdown": handoff_markdown(chat_id, extra)}


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

    return {"ok": remove_bookmark(bookmark_id)}


@app.get("/observability/usage")
async def api_usage():
    traces = list_traces(limit=80)
    tin = sum(int(t.get("token_input") or 0) for t in traces)
    tout = sum(int(t.get("token_output") or 0) for t in traces)
    last = traces[-1] if traces else {}
    return {
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
    set_current_chat(body.chat_id)
    return {}


@app.get("/chat/current-id")
async def api_get_current_chat_id():
    return {"chat_id": get_current_chat_id()}


@app.get("/chat/read/{chat_id}")
async def api_read_chat_log(chat_id: str):
    return read_chat_log(chat_id)


@app.delete("/chat/{chat_id}")
async def api_delete_chat(chat_id: str):
    """Delete a chat by id. Returns ok and deleted=true if the chat was removed."""
    deleted = delete_chat(chat_id)
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
    """Ingest a chat's history into the vector store for retrieval. Requires OPENAI_API_KEY."""
    try:
        api_key = get_openai_api_key()
    except Exception as e:
        return {"ok": False, "error": str(e), "chunks_added": 0}
    store = get_memory_store()
    n = ingest_chat(store, api_key, body.chat_id)
    return {"ok": True, "chunks_added": n}


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
    set_chats_storage_path(body.path)
    return {}


# --- Settings: LLM model / provider ---
@app.get("/settings/model")
async def api_get_model():
    """Return current LLM provider (openai or xai)."""
    return {"provider": get_llm_provider()}


@app.post("/settings/model")
async def api_set_model(body: SetModelRequest):
    """Set LLM provider to openai or xai."""
    set_llm_provider(body.provider)
    return {"provider": get_llm_provider()}


@app.get("/settings/runtime")
async def api_get_runtime():
    return {
        "provider": get_llm_provider(),
        "autonomy": get_autonomy_level(),
        "desktop_armed": is_desktop_armed(),
        "packaged": is_packaged(),
        "keys": api_keys_status(),
    }


@app.post("/settings/autonomy")
async def api_set_autonomy(body: SetAutonomyRequest):
    set_autonomy_level(body.autonomy)
    return {"autonomy": get_autonomy_level()}


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
        return ws_snapshot()
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
    from tools.workspace_io import list_rel_paths

    try:
        return {"ok": True, "paths": list_rel_paths()}
    except Exception as e:
        return {"ok": False, "error": str(e), "paths": []}


@app.get("/agent/pending")
async def api_agent_pending(chat_id: Optional[str] = None):
    from agents.hitl import list_pending

    return {"pending": list_pending(chat_id)}


@app.post("/agent/approve")
async def api_agent_approve(body: AgentApproveRequest):
    from agents.hitl import resolve_approval

    return resolve_approval(body.approval_id, bool(body.approve))


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
        sid, next_path = exchange_code_and_create_session(code=code, state=state)
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
    cases = generate_evals_from_logs(num_traces=num_traces, num_cases=num_cases)
    return {"generated": len(cases), "cases": [c.to_dict() for c in cases]}


@app.get("/observability/evals/cases")
async def api_get_eval_cases(limit: int = 100):
    return {"cases": [c.to_dict() for c in load_eval_cases(limit=limit)]}


@app.post("/observability/evals/run")
async def api_run_evals(case_limit: int = 20):
    """Run eval cases for all models (openai, xai); record pass@k."""
    runs = run_evals_for_all_models(case_limit=case_limit)
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
    return run_optimization_step()


@app.post("/observability/human-eval")
async def api_human_eval(max_problems: int = 5):
    """Run HumanEval benchmark for each model (optional; needs datasets)."""
    return run_human_eval_benchmark(max_problems=max_problems)


# --- File upload for attachments (web: frontend sends files as multipart) ---
@app.post("/chat/send-message-with-files")
async def send_message_with_files(
    request: Request,
    message: str = Form(""),
    chat_id: Optional[str] = Form(None),
    web_search_query: Optional[str] = Form(None),
    coding_mode: bool = Form(False),
    coding_project_snapshot: Optional[str] = Form(None),
    files: list[UploadFile] = File(default=[]),
):
    """Accept multipart form: message + files. Saves files to temp and calls send_message."""
    import tempfile
    import os
    paths = []
    try:
        for f in files:
            if not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1] or ".bin"
            fd, path = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            with open(path, "wb") as out:
                out.write(await f.read())
            paths.append(path)
        body = SendMessageRequest(
            message=message.strip(),
            attachment_paths=paths if paths else None,
            chat_id=chat_id,
            web_search_query=(web_search_query or "").strip() or None,
            coding_mode=bool(coding_mode),
            coding_project_snapshot=(coding_project_snapshot or "").strip() or None,
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
    await ws.accept()
    _ws_connections.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
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
        description="Directory to search (defaults to grep.default_root in ada-config.yaml or ada-grep-root.txt)",
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
        base = Path(raw_root).expanduser()
    else:
        gr = get_grep_root()
        if gr is None:
            return {
                "ok": False,
                "error": "No search root: pass root= or set grep.default_root in ada-config.yaml or create ada-grep-root.txt with a directory path.",
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
        return {"ok": False, "error": "empty query", "results_text": ""}
    text = await asyncio.to_thread(search_web, q)
    return {"ok": True, "query": q, "results_text": text}


@app.post("/tools/python-sandbox")
async def api_tools_python_sandbox(body: PythonSandboxRequest):
    """
    Execute Python in a sandboxed child process (timeout, restricted imports/builtins).
    For agents/models: prefer this over exec on the server process.
    """
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
        return {
            "ok": False,
            "error": "Shell disabled on server (remove ADA_DISABLE_SHELL or set ADA_ENABLE_SHELL=1).",
        }
    from agents.hitl import maybe_gate_shell

    result = await asyncio.to_thread(
        maybe_gate_shell,
        body.command,
        execute=lambda: run_shell_command(body.command, body.timeout_sec),
    )
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "packaged": is_packaged()}


if __name__ == "__main__":
    import os

    import uvicorn

    # So imports (config, agents, …) work even if you run from repo root
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("UVICORN_RELOAD", "1").lower() not in ("0", "false", "no")
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=reload)
