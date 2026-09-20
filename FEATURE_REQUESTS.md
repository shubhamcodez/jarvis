# Feature requests we designed Ada around

Public asks from Cursor, Claude Code, Codex, Devin, OpenHands, and OpenClaw (2026 forums + GitHub), plus what actually moves SWE-bench / Aider / HumanEval scores.

## What people keep asking for

| Theme | Who | Ada now |
| --- | --- | --- |
| Isolated git worktrees / apply-or-discard | Cursor, Claude Code `#17774`, Devin Local | Overlay worktree: edits stay off disk until apply; tests run on a temp materialize |
| Localize → patch → test, not one-shot dumps | SWE-agent, OpenHands, Agentless | `swe_loop`: grep/read/patch/test/critic |
| Deterministic verification | Anthropic long-running harness, OpenAI “taste as linters” | `diagnostics.py` compile + unittest/pytest. The model cannot declare victory |
| Critic / best-of-N / retry | OpenHands critic, Codex subagent wake | Separate critic; auto-retry when tests fail |
| Compact stable tools | 2026 harness blueprint | list_dir, grep, read_file, apply_patch, write_file, run_tests, run_python, update_plan, write_note, finish |
| AGENTS.md / project rules | Devin, Claude Code, Cursor | Loaded from the workspace (`AGENTS.md`, `ADA.md`, `CLAUDE.md`) |
| Artifact / filesystem memory | Blueprint, Claude Code compaction | `ada-artifacts/` handles for notes and localization |
| Checkpoints / resume | Claude Code `/checkpoint`, OpenClaw overnight agents | Existing run_control checkpoints + overlay export |
| LSP / diagnostics after edit | LangChain dcode roadmap, Devin | Syntax check after every patch; tests as the real sensor |
| Parallel / isolated agents | Codex multi-agent, Claude subagents, Cursor `/side` | Explore (read-only) + evaluate (critic) subagents |
| Conversation branch/merge | Claude Code `#32631` | Not first-class yet — checkpoints cover rewind |
| Cross-session wake on worker done | Codex FR | Existing child register/finish in the router |
| Cost / stop / spend caps | Reddit across tools | Existing run_control spend + cancel |
| Portable `AGENTS.md` + `.agents/skills/` | Claude Code `#6235` (3.6k 👍), `#50778` | Workspace `SKILL.md` discovery (`.agents/`, `.cursor/`, `.claude/`, `.ada/`) — catalog always, body on match |
| Native GitHub issue fetch | Claude Code `#10998` | `gh issue view` when the user says implement/fix `#N` |
| `/recap` structured recovery | Codex community FR | `/recap` — task, last result, files, next step (not a dump) |
| `/btw` / `/side` without polluting the main thread | Codex, Cursor `/side` | Chat-only side question via `/chat/response` |
| Search old sessions | r/ClaudeCode `/search-memory` | `/search-memory …` over Ada chat logs |
| Thumbs on replies | Claude Code `#89824`, `#25164` | 👍 / 👎 on assistant messages; down votes write a fact |
| Adaptive subagents + goal memory | Cursor forum 163991 | Goal lives in task_spec + artifacts; explore/evaluate subagents |
| Worktree cleanup hooks | Cursor forum 139624 | Overlay temps are deleted after tests |
| Context leftover / compact control | Claude Code `#1157`, Reddit tips | `/compact`, `/recap`, history compaction in prompt_assembly |
| Elide old tool results | arXiv 2608.26218 (28%→49% F2PF) | SWE loop already clips + compact after 20 turns |
| LSP / code-graph for cheaper localization | FalkorDB SWE-bench harness | Cheap grep localize; full LSP still open |
| Interactive HTML/SVG previews | OpenHands `#2691` | Not yet |

## What the benchmarks reward

- **SWE-bench Verified / SWE-Gym / SWE-bench-Live:** find the right file, small patch, run the repo tests, iterate on failures.
- **Aider Polyglot:** unique search-replace, not full-file rewrites.
- **HumanEval / LiveCodeBench:** complete a function and execute a hidden `check()`.
- **GAIA / AgentBench / τ-bench:** tools + unambiguous answers, not chat vibes.
- **OpenHands critic papers:** filter failing tests, then score remaining trajectories.

So Ada’s coding path is no longer “write a sandbox script and dump whole files.” Linked-repo work uses the SWE loop. Plots/math still use the sandbox.

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
