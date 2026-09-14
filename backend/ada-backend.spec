# PyInstaller spec for the Ada FastAPI sidecar (Windows).
# Run from backend/: poetry run pyinstaller ada-backend.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
here = Path(SPECPATH)

datas = []
for name in ("ada-config.yaml",):
    p = here / name
    if p.exists():
        datas.append((str(p), "."))

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
):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    [str(here / "ada_sidecar.py")],
    pathex=[str(here)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden + ["main", "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="ada-backend",
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
