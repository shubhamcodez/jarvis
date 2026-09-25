# PyInstaller spec for the Jarvis FastAPI sidecar (Windows).
# Run from backend/: poetry run pyinstaller jarvis-backend.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
here = Path(SPECPATH)

datas = []
for name in ("jarvis-config.yaml", "ada-config.yaml"):
    p = here / name
    if p.exists():
        datas.append((str(p), "."))
worker = here / "tools" / "sandbox_worker.py"
if worker.exists():
    datas.append((str(worker), "tools"))

hidden = []
for pkg in (
    "uvicorn",
    "fastapi",
    "starlette",
    "anyio",
    "langgraph",
    "langchain_core",
    "langchain_openai",
    "openai",
    "httpx",
    "yaml",
    "PIL",
    "mss",
    "pyautogui",
    "yfinance",
    "numpy",
    "pandas",
    "matplotlib",
    "websockets",
    "multipart",
    "dotenv",
    "agents",
    "memory",
    "tools",
    "auth",
    "custom_agents",
    "observability",
    "integrations",
):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    [str(here / "jarvis_sidecar.py")],
    pathex=[str(here)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden + ["main", "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["langchain_openai.middleware", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="jarvis-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
