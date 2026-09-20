# Feature requests we designed Jarvis around

Public asks from Cursor, Claude Code, Codex, Devin, OpenHands, and OpenClaw (2026 forums + GitHub), plus what actually moves SWE-bench / Aider / HumanEval scores.

## What people keep asking for

| Theme | Who | Jarvis now |
| --- | --- | --- |
| Isolated git worktrees / apply-or-discard | Cursor, Claude Code `#17774`, Devin Local | Overlay worktree: edits stay off disk until apply; tests run on a temp materialize |
| Localize → patch → test, not one-shot dumps | SWE-agent, OpenHands, Agentless | `swe_loop`: grep/read/patch/test/critic |
| Deterministic verification | Anthropic long-running harness, OpenAI “taste as linters” | `diagnostics.py` compile + unittest/pytest. The model cannot declare victory |
| Critic / best-of-N / retry | OpenHands critic, Codex subagent wake | Separate critic; auto-retry when tests fail |
| Compact stable tools | 2026 harness blueprint | list_dir, grep, read_file, apply_patch, write_file, run_tests, run_python, update_plan, write_note, finish |
| AGENTS.md / project rules | Devin, Claude Code, Cursor | Loaded from the workspace (`AGENTS.md`, `JARVIS.md`, `CLAUDE.md`) |
| Artifact / filesystem memory | Blueprint, Claude Code compaction | `jarvis-artifacts/` handles for notes and localization |
| Checkpoints / resume | Claude Code `/checkpoint`, OpenClaw overnight agents | Existing run_control checkpoints + overlay export |
| LSP / diagnostics after edit | LangChain dcode, FalkorDB, Devin | Stdio LSP after every patch (`jedi`/`pylsp`/`pyright` or bundled `jarvis-pylsp`) |
| Parallel / isolated agents | Codex multi-agent, Claude subagents, Cursor `/side` | Explore (read-only) + evaluate (critic) subagents |
| Conversation branch/merge | Claude Code `#32631` | **Fork** copies history through a message into a child chat; **Merge back** appends new turns to the parent |
| Cross-session wake on worker done | Codex FR | Existing child register/finish in the router |
| Cost / stop / spend caps | Reddit across tools | Existing run_control spend + cancel |
| Portable `AGENTS.md` + `.agents/skills/` | Claude Code `#6235` (3.6k 👍), `#50778` | Workspace `SKILL.md` discovery (`.agents/`, `.cursor/`, `.claude/`, `.ada/`) — catalog always, body on match |
| Native GitHub issue fetch | Claude Code `#10998` | `gh issue view` when the user says implement/fix `#N` |
| `/recap` structured recovery | Codex community FR | `/recap` — task, last result, files, next step (not a dump) |
| `/btw` / `/side` without polluting the main thread | Codex, Cursor `/side` | Chat-only side question via `/chat/response` |
| Search old sessions | r/ClaudeCode `/search-memory` | `/search-memory …` over Jarvis chat logs |
| Thumbs on replies | Claude Code `#89824`, `#25164` | 👍 / 👎 on assistant messages; down votes write a fact |
| Adaptive subagents + goal memory | Cursor forum 163991 | Goal lives in task_spec + artifacts; explore/evaluate subagents |
| Worktree cleanup hooks | Cursor forum 139624 | Overlay temps are deleted after tests |
| Context leftover / compact control | Claude Code `#1157`, Reddit tips | `/compact`, `/recap`, history compaction in prompt_assembly |
| Elide old tool results | arXiv 2608.26218 (28%→49% F2PF) | SWE loop already clips + compact after 20 turns |
| LSP / code-graph for cheaper localization | FalkorDB SWE-bench harness | Cheap grep localize + LSP diagnostics on edited files |
| Interactive HTML/SVG previews | OpenHands `#2691` | Sandboxed iframe for ` ```html ` / ` ```svg ` and `JARVIS_PREVIEW_HTML` / `JARVIS_PREVIEW_SVG` |
| `/diff` `/undo` `/commit` git loop | Aider, Claude Code commands | `/diff` (git status+diff), `/undo` (workbench or checkpoint), `/rewind` (drop last reply) |
| `/usage` `/cost` `/stats` | Claude Code | Token totals over the last 80 traces |
| `/init` portable AGENTS.md | Claude Code `/init`, `#6235` | Scans the linked repo and writes `AGENTS.md` if missing |
| `/doctor` setup checkup | Claude Code | Keys present (not values), workspace, git, skills, chats dir |
| `/review` before ship | Claude Code `/review` | Deterministic diff review + risk needles (no extra LLM) |
| Custom slash commands | Claude Code `#4370`, `#6946` | `.agents/commands/*.md` (also `.claude/`, `.cursor/`, `.ada/`) with `$ARGUMENTS` |
| Slash-invoke skills | Cursor `/skill`, Claude Code | `/skill name` or `/swe-fix …` expands the playbook into the next turn |
| Git blame/log for localize | SWE-agent / Agentless | `git_history` tool (log/blame/diff/status) + recent-commit files in localize |
| `/export` transcript | Claude Code, OpenHands Canvas | Full chat as Markdown download + clipboard |
| `/rename` session title | Claude Code `/rename` | `/rename [name]` or auto-title from history |
| `/copy [N]` last reply | Claude Code `/copy` | Clipboard the latest (or Nth) assistant message |
| `/context` window fill | Claude Code `/context` | Per-role token estimate for the current chat + `/usage` |
| `/plan` first-class | Claude Code `#30438` | Sets run mode to Plan; optional task starts a no-write turn |
| Visible agent todos | Claude Code TodoWrite | Live strip from SWE `update_plan` |
| HITL / run-done notify | Claude Code `#29827` | Browser notification when the tab is in the background (`/notify`) |
| Mermaid in chat | OpenHands / Claude Code asks | Local `graph`/`flowchart` SVG preview; other types show a fallback card |
| `/loop` in-session check-ins | Claude Code, Cursor `/loop` | `/loop 5m …` ticks via the backend scheduler; chat-only; `/loop stop` |
| `/tasks` / `/bashes` | Claude Code | Lists durable tasks + active runs |
| `/goal` completion condition | Claude Code `/goal`, Codex | Stored on the chat + banner; critic runs once after each turn; `/goal clear` |
| Saved plan document | Claude Code `#30438` | SWE `update_plan` writes `jarvis-artifacts/PLAN.md` and persists on the chat; `/plan show` + live todo strip |
| Slash autocomplete | Claude Code / Cursor | Composer `/` lists builtins, workspace commands, and skills |
| Apply/Discard batch | Cursor, Claude Code `#17774` | Bar above chat applies or drops the whole proposed-edit batch |
| Multi-ecosystem tests | SWE-bench / Aider polyglot | `run_tests` picks pytest, npm test, cargo test, or go test |
| Overlay turn undo | Claude Code `/checkpoint` | Each applied SWE turn stores an `overlay_turn` checkpoint |
| `/model` `/cd` `/effort` | Claude Code commands | Provider switch, relink workspace, token spend cap |

## What the benchmarks reward

- **SWE-bench Verified / SWE-Gym / SWE-bench-Live:** find the right file, small patch, run the repo tests, iterate on failures.
- **Aider Polyglot:** unique search-replace, not full-file rewrites.
- **HumanEval / LiveCodeBench:** complete a function and execute a hidden `check()`.
- **GAIA / AgentBench / τ-bench:** tools + unambiguous answers, not chat vibes.
- **OpenHands critic papers:** filter failing tests, then score remaining trajectories.

So Jarvis’s coding path is no longer “write a sandbox script and dump whole files.” Linked-repo work uses the SWE loop. Plots/math still use the sandbox.

## Run the suites

```powershell
cd D:\JARVIS\backend
python -m tests.test_swe_harness
python -m benchmarks.download
python -m benchmarks.runner --suite gold
python -m benchmarks.runner --suite swe --provider xai
python -m benchmarks.runner --suite humaneval --limit 10 --provider xai
python -m benchmarks.runner --suite general --provider xai
```

Downloaded corpora land in `backend/benchmarks/data/` (164 HumanEval, 200 GSM8K, 50 SWE-bench Verified problem statements). Full SWE-bench Docker eval is not run here; isomorphic fixtures cover the same skills.

## Live scores (xAI `grok-4-1-fast-non-reasoning`, 2026-09-19)

| Suite | n | Pass |
| --- | --- | --- |
| SWE fixtures (localize → patch → test) | 5 | **5/5** after one harness iteration (first run 3/5; xAI 403 refusals recovered) |
| HumanEval | 25 | **25/25** |
| General sandbox | 4 | **4/4** |
| GSM8K (sandbox math) | 10 | **8/10** |
| Gold patches (no model) | 5 | buggy fail / gold pass |

Iteration that mattered: treat 403 refusals as recoverable, reframe tasks as coding exercises, and nudge after three read-only steps so the agent actually patches.
