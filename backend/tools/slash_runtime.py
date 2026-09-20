"""Local slash commands that do not start an agent turn."""
from __future__ import annotations

from typing import Any, Optional

from tools.agents_init import draft_agents_md, write_agents_md
from tools.doctor import doctor_markdown
from tools.git_history import git_diff, git_status, is_git_repo
from tools.slash_commands import catalog, resolve_named


def _usage_markdown() -> str:
    from observability.trace import list_traces

    traces = list_traces(limit=80)
    tin = sum(int(t.get("token_input") or 0) for t in traces)
    tout = sum(int(t.get("token_output") or 0) for t in traces)
    last = traces[-1] if traces else {}
    lines = [
        "# Usage",
        "",
        f"- Window: last {len(traces)} traces",
        f"- Tokens in: {tin}",
        f"- Tokens out: {tout}",
    ]
    if last:
        lines.append(
            f"- Last: route=`{last.get('route') or '?'}` in={last.get('token_input') or 0} "
            f"out={last.get('token_output') or 0} ({last.get('duration_sec') or 0}s)"
        )
    return "\n".join(lines)


def _diff_markdown(workspace_root: str) -> str:
    if not workspace_root:
        return "No workspace folder is linked."
    if not is_git_repo(workspace_root):
        return "Linked folder is not a git repository, so `/diff` has nothing to compare."
    st = git_status(workspace_root)
    unstaged = git_diff(workspace_root, staged=False)
    staged = git_diff(workspace_root, staged=True)
    parts = [
        "# Diff",
        "",
        "## Status",
        "```",
        (st.get("output") or "(clean)").strip() or "(clean)",
        "```",
        "",
        "## Unstaged",
        "```diff",
        (unstaged.get("diff") or "(none)")[:8000],
        "```",
    ]
    if staged.get("diff") and "no staged" not in (staged.get("diff") or ""):
        parts.extend(["", "## Staged", "```diff", staged["diff"][:4000], "```"])
    return "\n".join(parts)


def _review_markdown(workspace_root: str) -> str:
    if not workspace_root:
        return "No workspace folder is linked."
    if not is_git_repo(workspace_root):
        return "Linked folder is not a git repository."
    unstaged = git_diff(workspace_root, staged=False)
    diff = unstaged.get("diff") or ""
    flags: list[str] = []
    for needle, label in (
        ("eval(", "eval()"),
        ("exec(", "exec()"),
        ("subprocess", "subprocess"),
        ("os.system", "os.system"),
        ("innerHTML", "innerHTML"),
        ("dangerouslySetInnerHTML", "dangerouslySetInnerHTML"),
        ("TODO", "TODO left in"),
        ("password", "password literal"),
        ("api_key", "api_key"),
    ):
        if needle in diff:
            flags.append(label)
    lines = [
        "# Review",
        "",
        "Read-only pass over the current git diff (no model).",
        "",
        f"**Stat:** {(unstaged.get('stat') or 'n/a').strip() or 'n/a'}",
        "",
    ]
    if flags:
        lines.append("**Flags:** " + ", ".join(flags))
    else:
        lines.append("**Flags:** none of the cheap risk needles.")
    lines.extend(["", "```diff", diff[:6000] or "(clean working tree)", "```"])
    return "\n".join(lines)


def _catalog_markdown(workspace_root: Optional[str]) -> str:
    data = catalog(workspace_root)
    lines = ["# Commands & skills", ""]
    cmds = data.get("commands") or []
    skills = data.get("skills") or []
    if cmds:
        lines.append("## File-based commands")
        for c in cmds:
            hint = f" {c['argument_hint']}" if c.get("argument_hint") else ""
            lines.append(f"- `/{c['name']}{hint}` — {c.get('description') or ''}")
        lines.append("")
    if skills:
        lines.append("## Skills")
        for s in skills:
            lines.append(f"- `/{s['name']}` — {s.get('description') or ''}")
    if not cmds and not skills:
        lines.append("None in this workspace. Add `SKILL.md` under `.agents/skills/` or a `.md` under `.agents/commands/`.")
    return "\n".join(lines)


def run_slash(
    name: str,
    args: str = "",
    *,
    chat_id: str = "",
    workspace_root: Optional[str] = None,
) -> dict[str, Any]:
    from config import get_workspace_root

    cmd = (name or "").strip().lstrip("/").lower()
    extra = (args or "").strip()
    root = (workspace_root or get_workspace_root() or "").strip()

    if cmd in {"usage", "cost", "stats"}:
        return {"ok": True, "kind": "local", "markdown": _usage_markdown()}
    if cmd == "doctor":
        return {"ok": True, "kind": "local", "markdown": doctor_markdown()}
    if cmd == "diff":
        return {"ok": True, "kind": "local", "markdown": _diff_markdown(root)}
    if cmd == "review":
        return {"ok": True, "kind": "local", "markdown": _review_markdown(root)}
    if cmd in {"commands", "skills"}:
        return {"ok": True, "kind": "local", "markdown": _catalog_markdown(root)}
    if cmd == "init":
        force = extra.lower() in {"force", "--force", "overwrite"}
        if force:
            out = write_agents_md(force=True, workspace_root=root or None)
        else:
            out = write_agents_md(force=False, workspace_root=root or None)
            if not out.get("written") and out.get("ok") and out.get("exists"):
                return {
                    "ok": True,
                    "kind": "local",
                    "markdown": (
                        "AGENTS.md already exists. Not overwritten.\n\n"
                        "Send `/init force` to replace it.\n\n"
                        + (out.get("markdown") or "")
                    ),
                }
        if not out.get("ok"):
            return {"ok": False, "kind": "local", "markdown": out.get("error") or "init failed"}
        title = "Wrote `AGENTS.md`." if out.get("written") else "Draft only."
        return {"ok": True, "kind": "local", "markdown": f"{title}\n\n{out.get('markdown') or ''}"}
    if cmd == "rewind":
        from memory.chat_log import rewind_chat

        keep = None
        if extra.isdigit():
            keep = int(extra)
        result = rewind_chat(chat_id, keep_count=keep)
        if not result.get("ok"):
            return {"ok": False, "kind": "local", "markdown": result.get("error") or "rewind failed"}
        return {
            "ok": True,
            "kind": "local",
            "markdown": (
                f"Rewound chat. Kept {result.get('kept')} messages "
                f"(dropped {result.get('dropped')})."
            ),
            "reload": True,
        }
    if cmd == "undo":
        from agents.run_control import list_checkpoints, restore_checkpoint

        cps = list_checkpoints(limit=1)
        if not cps:
            return {
                "ok": True,
                "kind": "local",
                "markdown": "No server checkpoints. If you edited files in the workbench, use the workbench Undo.",
            }
        rec = restore_checkpoint(str(cps[0].get("id") or ""))
        if not rec.get("ok"):
            return {"ok": False, "kind": "local", "markdown": rec.get("error") or "undo failed"}
        return {
            "ok": True,
            "kind": "local",
            "markdown": f"Restored checkpoint `{rec.get('id')}` ({rec.get('kind')}) — `{rec.get('restored') or ''}`.",
        }
    if cmd == "rename":
        from memory.chat_log import rename_chat

        if not chat_id:
            return {"ok": False, "kind": "local", "markdown": "No active chat to rename."}
        out = rename_chat(chat_id, extra)
        if not out.get("ok"):
            return {"ok": False, "kind": "local", "markdown": out.get("error") or "rename failed"}
        return {
            "ok": True,
            "kind": "local",
            "markdown": f"Renamed this chat to **{out.get('title')}**.",
            "reload": True,
            "title": out.get("title"),
        }
    if cmd == "export":
        from memory.chat_log import export_chat_markdown

        if not chat_id:
            return {"ok": False, "kind": "local", "markdown": "No active chat to export."}
        out = export_chat_markdown(chat_id)
        if not out.get("ok"):
            return {"ok": False, "kind": "local", "markdown": out.get("error") or "export failed"}
        return {
            "ok": True,
            "kind": "export",
            "markdown": f"Exported **{out.get('message_count')}** messages as `{out.get('filename')}` (also copied when the browser allowed it).",
            "filename": out.get("filename"),
            "text": out.get("markdown"),
        }
    if cmd == "copy":
        from memory.chat_log import last_assistant_text

        nth = 1
        if extra.isdigit():
            nth = int(extra)
        if not chat_id:
            return {"ok": False, "kind": "local", "markdown": "No active chat."}
        out = last_assistant_text(chat_id, nth)
        if not out.get("ok"):
            return {"ok": False, "kind": "local", "markdown": out.get("error") or "nothing to copy"}
        return {
            "ok": True,
            "kind": "copy",
            "markdown": f"Copied assistant reply #{out.get('nth')} of {out.get('total')} to the clipboard.",
            "text": out.get("text"),
        }
    if cmd == "context":
        from memory.chat_log import chat_context_stats

        if not chat_id:
            return {"ok": False, "kind": "local", "markdown": "No active chat."}
        stats = chat_context_stats(chat_id)
        roles = stats.get("by_role") or {}
        role_line = ", ".join(f"{k}={v}" for k, v in roles.items()) or "n/a"
        usage = _usage_markdown()
        md = (
            "# Context\n\n"
            f"- Messages: {stats.get('messages')}\n"
            f"- Estimated tokens in this chat: {stats.get('tokens')}\n"
            f"- By role: {role_line}\n\n"
            + usage
        )
        return {"ok": True, "kind": "local", "markdown": md}
    if cmd == "plan":
        low = extra.lower()
        if low in {"show", "open"}:
            from tools.artifact_store import read_artifact

            art = read_artifact("PLAN.md")
            body = art.get("content") if art.get("ok") else "_No saved plan yet. The SWE agent writes `PLAN.md` when it calls update_plan._"
            return {"ok": True, "kind": "local", "markdown": f"# Saved plan\n\n{body}"}
        from config import set_run_mode

        set_run_mode("plan")
        if extra:
            return {
                "ok": True,
                "kind": "prompt",
                "prompt": extra,
                "set_mode": "plan",
            }
        return {
            "ok": True,
            "kind": "local",
            "markdown": "Run mode is now **Plan** (no writes). `/plan show` prints the last saved SWE plan. `/plan fix the auth bug` starts a planning turn.",
            "set_mode": "plan",
        }
    if cmd in {"loop", "proactive"}:
        from tools.session_loops import list_loops, loops_markdown, parse_interval, start_loop, stop_loops

        if extra.lower() in {"stop", "clear", "off"}:
            n = stop_loops(chat_id)
            return {"ok": True, "kind": "local", "markdown": f"Stopped {n} loop(s) for this chat."}
        if extra.lower() in {"list", "ls", ""}:
            return {"ok": True, "kind": "local", "markdown": loops_markdown(chat_id)}
        first, _, rest = extra.partition(" ")
        sec = parse_interval(first)
        prompt = rest.strip() if sec else extra
        if sec is None:
            sec = 300
        out = start_loop(chat_id, sec, prompt)
        if not out.get("ok"):
            return {"ok": False, "kind": "local", "markdown": out.get("error") or "loop failed"}
        return {
            "ok": True,
            "kind": "loop",
            "markdown": (
                f"Loop started: every **{out.get('interval_sec')}s** — {out.get('prompt')}\n\n"
                f"First fire in that interval while the backend is up. `/loop stop` cancels. "
                f"Check-ins are chat-only. Prompts that mention tests also run workspace tests read-only."
            ),
            "interval_sec": out.get("interval_sec"),
            "loop_id": out.get("id"),
        }
    if cmd in {"tasks", "bashes"}:
        from agents.run_control import list_active
        from agents.tasks import list_tasks

        tasks = list_tasks(chat_id=chat_id or None, open_only=False, limit=20)
        runs = list_active(chat_id or None)
        lines = ["# Tasks", ""]
        if not tasks:
            lines.append("No durable tasks.")
        for t in tasks:
            lines.append(
                f"- `{t.get('id')}` **{t.get('status')}** — {t.get('title') or t.get('goal') or ''}"
                + (f" (next: {t.get('next_action')})" if t.get("next_action") else "")
            )
        lines.extend(["", "# Active runs", ""])
        if not runs:
            lines.append("No in-flight runs.")
        for r in runs:
            lines.append(f"- `{r.get('run_id')}` {r.get('status')} tokens={r.get('tokens_in') or 0}/{r.get('tokens_out') or 0}")
        return {"ok": True, "kind": "local", "markdown": "\n".join(lines)}
    if cmd == "goal":
        from memory.chat_log import get_chat_meta, set_chat_goal

        if not chat_id:
            return {"ok": False, "kind": "local", "markdown": "No active chat."}
        if extra.lower() in {"clear", "off", "stop"}:
            set_chat_goal(chat_id, "")
            return {"ok": True, "kind": "local", "markdown": "Goal cleared.", "reload": True}
        if not extra:
            meta = get_chat_meta(chat_id)
            g = meta.get("goal_condition") or ""
            return {
                "ok": True,
                "kind": "local",
                "markdown": f"**Goal:** {g}" if g else "No goal set. `/goal tests pass` keeps the condition on this chat.",
            }
        set_chat_goal(chat_id, extra)
        return {
            "ok": True,
            "kind": "local",
            "markdown": f"Goal set: **{extra}**. After each turn Ada runs the critic once against this condition. Use `/loop` to keep checking, or `/goal clear`.",
            "reload": True,
        }
    if cmd == "model":
        from config import get_llm_provider, set_llm_provider

        if not extra:
            return {"ok": True, "kind": "local", "markdown": f"Current model provider: **{get_llm_provider()}** (`openai` / `xai` / `local`)."}
        try:
            set_llm_provider(extra)
        except ValueError as e:
            return {"ok": False, "kind": "local", "markdown": str(e)}
        return {"ok": True, "kind": "local", "markdown": f"Provider is now **{get_llm_provider()}**.", "set_provider": extra.lower()}
    if cmd == "cd":
        from tools.workspace_io import link_workspace, status as ws_status

        if not extra:
            st = ws_status()
            return {
                "ok": True,
                "kind": "local",
                "markdown": f"Workspace: `{st.get('path') or '(none linked)'}`",
            }
        out = link_workspace(extra)
        if not out.get("ok"):
            return {"ok": False, "kind": "local", "markdown": out.get("error") or "could not link"}
        return {"ok": True, "kind": "local", "markdown": f"Linked workspace `{out.get('path')}`."}
    if cmd == "effort":
        from config import get_spend_limits, set_spend_limits

        table = {"low": 20_000, "medium": 80_000, "high": 200_000, "max": 400_000}
        if not extra:
            cur = get_spend_limits()
            return {"ok": True, "kind": "local", "markdown": f"Effort cap: **{cur.get('max_tokens_per_run')}** tokens/run. Set `/effort low|medium|high`."}
        key = extra.lower()
        if key not in table:
            return {"ok": False, "kind": "local", "markdown": "Use `/effort low`, `medium`, `high`, or `max`."}
        set_spend_limits(max_tokens_per_run=table[key], warn_tokens=table[key] // 2)
        return {"ok": True, "kind": "local", "markdown": f"Spend cap is now **{table[key]}** tokens per run ({key})."}
    if cmd == "notify":
        return {
            "ok": True,
            "kind": "notify",
            "markdown": "Desktop notifications: Ada will alert you when a run finishes or needs approval if this tab is in the background.",
        }
    if cmd == "skill":
        token, _, rest = extra.partition(" ")
        resolved = resolve_named(token, rest, root or None)
        if not resolved.get("ok"):
            return {"ok": False, "kind": "unknown", "markdown": resolved.get("error") or "unknown skill"}
        return {"ok": True, "kind": "prompt", "prompt": resolved["prompt"], "name": resolved.get("name")}

    resolved = resolve_named(cmd, extra, root or None)
    if resolved.get("ok"):
        return {"ok": True, "kind": "prompt", "prompt": resolved["prompt"], "name": resolved.get("name")}
    return {"ok": False, "kind": "unknown", "error": f"unknown command /{cmd}"}
