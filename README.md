# Ada

Local AI assistant: chat, coding workspace, desktop/shell/finance/Google agents.

- **Browser (dev):** `npm run dev:all` → http://localhost:5173 (backend on `127.0.0.1:8000`)
- **Desktop:** see [DESKTOP.md](DESKTOP.md) — Tauri window + Python sidecar, Windows installer `Ada_*_x64-setup.exe`
- **What users of Claude Code / Cursor / Codex / Devin / OpenHands ask for:** [FEATURE_REQUESTS.md](FEATURE_REQUESTS.md)
- **Coding harness:** localize → unique patch → tests → critic (`backend/agents/swe_loop.py`)
- **Benchmarks:** `python -m benchmarks.download` then `python -m benchmarks.runner --suite swe` (see FEATURE_REQUESTS.md)
