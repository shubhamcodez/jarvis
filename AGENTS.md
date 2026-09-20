# AGENTS.md

Ada is a local desktop assistant (FastAPI + LangGraph + Tauri).

## Setup

- Backend: `cd backend` then Poetry/uvicorn on port 8000
- Frontend: Vite on 5173 (`npm run dev:all` from repo root)
- Secrets in `.env` (`OPENAI_API_KEY` / `XAI_API_KEY`) — never commit them

## Coding agent

- Linked workspace + repo task → `agents/swe_loop.py` (overlay, patch, tests, critic)
- Plots / numeric scripts → Python sandbox (`backend/SANDBOX.md`)
- Prefer `apply_patch` over rewriting whole files
- Do not modify `backend/benchmarks/fixtures/*/tests`

## Tests

```text
python -m tests.test_swe_harness
python -m benchmarks.runner --suite gold
```

## Style

- Keep the built-in tool catalog small
- Put guardrails in runtime code, not only prompts
- Deterministic checks beat “the model said it worked”
