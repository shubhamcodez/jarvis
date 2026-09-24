# Jarvis

Jarvis is a local desktop assistant for Windows. You chat with it in a window on your computer. It can help with everyday questions, coding in a linked project, market quotes, and (after you sign in) Google Calendar and Gmail.

This guide is for **version 0.0.2**. You do not need Python, Node, or Rust to use the installer.

## Run the installer

1. Double-click `Jarvis_0.0.2_x64-setup.exe`. Windows SmartScreen may say the publisher is unknown because this build is not code-signed. Choose **More info**, then **Run anyway**.
2. The installer is for the current Windows user. It does not need administrator rights.
3. If Windows asks to install the **WebView2** runtime, allow it. Jarvis uses it to draw the window.
4. When setup finishes, open **Jarvis** from the Start menu.

Close Jarvis before you install an update. Setup stops the app so it can replace its files. If a file is still locked, restart Windows and run the installer again.

### First launch

1. Open **Settings**.
2. Paste an **OpenAI** key, an **xAI** key, or both, then choose **Save keys**.
3. Keys stay in `%APPDATA%\Jarvis\.env` on this PC. They are not inside the installer.
4. Go back to chat and send a message.

Without a key, chat cannot reach a cloud model. You can also download a suggested local model from Settings if you want replies to stay on this machine.

### Where your data lives

| What | Where |
| --- | --- |
| The app | `%LOCALAPPDATA%\Jarvis\` |
| Chats, keys, and agent state | `%APPDATA%\Jarvis\` |

Uninstalling the app does not delete that folder. Copy it if you want a backup.

### If something looks wrong

- **SmartScreen or antivirus warning.** Unsigned builds and the bundled assistant runtime are often flagged. You can continue if you trust the file you downloaded.
- **Window opens, then nothing answers.** Open Settings and confirm a key is saved, or that a local model finished downloading.
- **“Host shell” tasks do nothing.** In the installed app, terminal commands stay off until you set `ADA_ENABLE_SHELL=1` in `%APPDATA%\Jarvis\.env` and restart Jarvis. Leave this off unless you want Jarvis to run commands on your PC.
- **Google sign-in fails.** In Google Cloud, add the redirect `http://127.0.0.1:8000/auth/google/callback` and the JavaScript origin `https://tauri.localhost`.

## Version 0.0.2

| | |
| --- | --- |
| Version | 0.0.2 |
| Platform | Windows 64-bit |
| Installer | `Jarvis_0.0.2_x64-setup.exe` |
| Needs | Windows 10 or later, WebView2, and an API key or a downloaded local model |

## Features

- **Chat.** Start a new conversation, search older ones, and keep going later. Say `remember …` to store a fact Jarvis can use again.
- **Agent, Plan, and Draft.** Next to the message box: **Agent** follows your autonomy setting, **Plan** will not run shell commands, write files, send email, or drive the screen, and **Draft** asks before those actions.
- **Autonomy and spend.** Settings control how much Jarvis may do on its own. A token cap stops a single run that grows too large. Quiet hours hold scheduled routines until the window ends.
- **Coding.** Turn coding mode on in Settings, link a project, and review proposed edits before you apply or discard them. Tests can run for Python, npm, Cargo, and Go projects.
- **Custom agents.** Build an agent with its own profile, memory, skills, tools, and schedule.
- **Finance.** Ask for a quote, a P/E, or a short comparison. Charts and heavier number work run in a local Python sandbox.
- **Google.** After you connect an account, Jarvis can list and edit Calendar events and read or send Gmail. Sending and deleting still wait for your confirmation.
- **Desktop help.** Jarvis can walk through on-screen steps. It asks before it clicks or types for you.
- **Local model.** Settings can detect your GPU or NPU and download a suggested model so chat does not have to use the cloud.

Useful chat commands:

| Command | What it does |
| --- | --- |
| `/plan` | Switch to Plan for the next task |
| `/recap` | Short summary of the current task |
| `/diff` | Show git status and the current diff |
| `/undo` | Drop the last unapplied workbench change |
| `/doctor` | Check keys, workspace, and git without printing secrets |
| `/usage` | Token totals for recent runs |

## Changelog

### 0.0.2

First desktop installer for everyday use.

- Windows setup file that installs Jarvis for the current user, including the local assistant runtime. Python and Node are not required.
- Settings page for API keys, autonomy, spend cap, quiet hours, identity, and an optional local model download.
- Chat with searchable history, facts memory, and Agent / Plan / Draft.
- Linked-project coding with review before apply, plus finance, Google Calendar and Gmail, and desktop help.
- Packaged installs keep host shell commands off unless you opt in.

## Contribution guidelines

Issues and pull requests are welcome. Please keep each change small and easy to review.

1. Do not commit secrets. That includes `.env`, API keys, and OAuth client secrets.
2. Do not edit the checked-in tests under `backend/benchmarks/fixtures/*/tests`. Those files are the benchmark, not the product.
3. Describe what you changed and how you checked it. A short note in the pull request is enough.
4. Match the style of the file you touch. Prefer a small patch over rewriting a whole file.

### Build and test (developers)

You need Node 20+, Python 3.11+, Poetry, and Rust.

```powershell
cd backend
poetry install
poetry run uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

In a second terminal, from the repo root:

```powershell
npm install
npm run tauri:dev
```

Browser-only (no desktop window): `npm run dev:all`, then open http://localhost:5173.

To produce the installer:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
```

The setup file is written under `src-tauri/target/release/bundle/nsis/` and copied to `dist/`.

Checks that should pass before you open a pull request:

```powershell
cd backend
python -m tests.test_swe_harness
python -m benchmarks.runner --suite gold
```

More detail on the desktop build is in [DESKTOP.md](DESKTOP.md). Agent and coding-harness notes are in [AGENTS.md](AGENTS.md).
