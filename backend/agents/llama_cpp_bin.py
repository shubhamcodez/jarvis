"""Download a prebuilt llama.cpp server for this OS/arch and run local GGUF models."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

import httpx

from config import data_root

LLAMA_PORT = 8099
_GH_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=25"
_PROC: subprocess.Popen | None = None


def runtime_dir() -> Path:
    p = data_root() / "models" / "llama-cpp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _is_arm64() -> bool:
    return platform.machine().lower() in ("arm64", "aarch64")


def _hw_devices() -> list[dict[str, Any]]:
    try:
        from agents.hardware import detect_hardware

        return list(detect_hardware().get("devices") or [])
    except Exception:
        return []


def _asset_preferences() -> list[str]:
    """Ordered llama.cpp asset name fragments for this machine."""
    try:
        from agents.hardware import detect_hardware

        hint = str(detect_hardware().get("recommended_runtime") or "")
    except Exception:
        hint = ""
    devices = _hw_devices()
    names = " ".join(str(d.get("name") or "") for d in devices).lower()
    vendors = {str(d.get("vendor") or "") for d in devices}
    nvidia = any(d.get("vendor") == "nvidia" and not d.get("memory_shared") for d in devices)
    amd = "amd" in vendors
    adreno = "adreno" in names or "qualcomm" in vendors
    arm = _is_arm64()

    prefs: list[str] = []
    if hint:
        prefs.append(hint)
    if sys.platform == "win32":
        if arm:
            if adreno:
                prefs += ["win-opencl-adreno-arm64", "win-cpu-arm64"]
            else:
                prefs += ["win-cpu-arm64"]
        else:
            if nvidia:
                prefs += ["win-cuda-12.4-x64", "win-cuda-13.3-x64", "win-vulkan-x64", "win-cpu-x64"]
            elif amd:
                prefs += ["win-vulkan-x64", "win-rocm", "win-cpu-x64"]
            else:
                prefs += ["win-vulkan-x64", "win-cpu-x64"]
    elif sys.platform == "darwin":
        prefs += ["macos-arm64", "macos-x64"] if arm else ["macos-x64"]
    else:
        if arm:
            prefs += ["ubuntu-cpu-arm64", "bin-ubuntu"]
        elif nvidia:
            prefs += ["ubuntu-cuda", "ubuntu-vulkan-x64", "ubuntu-cpu-x64", "ubuntu-x64"]
        else:
            prefs += ["ubuntu-cpu-x64", "ubuntu-vulkan-x64", "ubuntu-x64"]
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in prefs:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _is_archive(name: str) -> bool:
    n = name.lower()
    return n.endswith(".zip") or n.endswith(".tar.gz") or n.endswith(".tgz")


def _pick_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    assets = [a for a in (release.get("assets") or []) if _is_archive(str(a.get("name") or ""))]
    if not assets:
        return None
    for pref in _asset_preferences():
        for a in assets:
            name = str(a.get("name") or "").lower()
            if pref.lower() in name and "bin-" in name:
                return a
    return None


def _find_server_exe(root: Path) -> Path | None:
    names = ("llama-server.exe", "llama-server")
    for n in names:
        direct = root / n
        if direct.is_file():
            return direct
    for p in root.rglob("llama-server.exe"):
        return p
    for p in root.rglob("llama-server"):
        if p.is_file():
            return p
    return None


def binary_path() -> Optional[Path]:
    marker = runtime_dir() / "current.json"
    if marker.exists():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            exe = Path(data.get("exe") or "")
            if exe.is_file():
                return exe
        except (OSError, json.JSONDecodeError):
            pass
    return _find_server_exe(runtime_dir())


def available() -> bool:
    return binary_path() is not None


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest)
        return
    with tarfile.open(archive, "r:*") as tf:
        tf.extractall(dest)


def ensure_binary(progress_cb=None) -> Path:
    existing = binary_path()
    if existing:
        return existing
    if progress_cb:
        progress_cb("Fetching llama.cpp release metadata")
    with httpx.Client(timeout=90.0, follow_redirects=True) as client:
        r = client.get(
            _GH_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ada-desktop"},
        )
        r.raise_for_status()
        releases = r.json()
        if not isinstance(releases, list) or not releases:
            raise RuntimeError("Could not list llama.cpp releases.")
        asset = None
        tag = ""
        for rel in releases:
            if not any(_is_archive(str(a.get("name") or "")) for a in (rel.get("assets") or [])):
                continue
            asset = _pick_asset(rel)
            if asset:
                tag = str(rel.get("tag_name") or "latest")
                break
        if not asset:
            raise RuntimeError(
                "No llama.cpp build matches this OS/arch. "
                f"Looked for: {', '.join(_asset_preferences()) or 'unknown'}."
            )
        url = asset.get("browser_download_url")
        name = asset.get("name") or "llama.cpp.bin"
        dest_file = runtime_dir() / name
        if progress_cb:
            progress_cb(f"Downloading {name}")
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest_file.open("wb") as f:
                for chunk in resp.iter_bytes(1024 * 256):
                    f.write(chunk)
    extract = runtime_dir() / tag
    if progress_cb:
        progress_cb("Extracting llama.cpp")
    _extract(dest_file, extract)
    exe = _find_server_exe(extract)
    if not exe:
        raise RuntimeError("llama-server was not in the downloaded archive.")
    if sys.platform != "win32":
        try:
            exe.chmod(exe.stat().st_mode | 0o111)
        except OSError:
            pass
    (runtime_dir() / "current.json").write_text(
        json.dumps({"tag": tag, "exe": str(exe), "asset": name}, indent=2),
        encoding="utf-8",
    )
    return exe


def stop_server() -> None:
    global _PROC
    if _PROC is None:
        return
    try:
        _PROC.terminate()
        _PROC.wait(timeout=8)
    except Exception:
        try:
            _PROC.kill()
        except Exception:
            pass
    _PROC = None


def start_server(model_path: str, n_ctx: int = 4096) -> str:
    """Start llama-server and return the OpenAI-compatible base URL."""
    global _PROC
    stop_server()
    exe = ensure_binary()
    args = [
        str(exe),
        "-m",
        model_path,
        "--host",
        "127.0.0.1",
        "--port",
        str(LLAMA_PORT),
        "-c",
        str(n_ctx),
        "--jinja",
    ]
    _PROC = subprocess.Popen(
        args,
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{LLAMA_PORT}"
    deadline = time.time() + 90
    last_err = ""
    while time.time() < deadline:
        if _PROC.poll() is not None:
            raise RuntimeError("llama-server exited before it became healthy.")
        try:
            with httpx.Client(timeout=2.0) as client:
                h = client.get(f"{base}/health")
                if h.status_code < 500:
                    return f"{base}/v1"
        except Exception as e:
            last_err = str(e)
        time.sleep(0.4)
    raise RuntimeError(f"llama-server did not start: {last_err or 'timeout'}")


def openai_base_url() -> str:
    return f"http://127.0.0.1:{LLAMA_PORT}/v1"
