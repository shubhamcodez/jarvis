# Ada desktop (Windows)

Ada is a **desktop app**: a Tauri window around the existing React UI, with the Python agent runtime as a localhost sidecar (`127.0.0.1:8000`).

## Develop

Need: Node 20+, Python 3.11+, Poetry, Rust (`https://rustup.rs`).

```bash
# Terminal 1 — or let `tauri:dev` spawn this
cd backend
poetry install
poetry run uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2
npm install
npm run tauri:dev
```

Browser-only (no Tauri): `npm run dev:all` then open http://localhost:5173.

## Installer (`Ada_*_x64-setup.exe`)

On a Windows build machine:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
```

Output: `src-tauri/target/release/bundle/nsis/Ada_0.1.0_x64-setup.exe`

The installer does **not** require Python, Poetry, or Node. First launch: Settings → paste API keys (stored in `%APPDATA%\Ada\.env`).

Unsigned builds may show SmartScreen — **More info → Run anyway**. Code signing is a follow-on.

This repo’s Tauri crate compiles on both x64 and ARM64 Windows (`rustc` target triple is whatever `rustup` detects).

**Packaged safety:** host shell is **off** unless `ADA_ENABLE_SHELL=1`. Google OAuth uses loopback `http://127.0.0.1:8000/auth/google/callback` (add that URI in GCP; JS origin `https://tauri.localhost`).

Antivirus may flag PyInstaller + desktop automation (`pyautogui`).

## Data

| | Path |
| --- | --- |
| App | `%LOCALAPPDATA%\Ada\` (typical) |
| Chats, keys, agent state | `%APPDATA%\Ada\` |
