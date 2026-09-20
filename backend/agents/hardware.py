"""Detect GPU / NPU / RAM on Windows, macOS, and Linux (dev or packaged install)."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
_DETECT_LOCK = threading.Lock()
_PROBE_LOCK = threading.Lock()
_PROBE_RUNNING = False
_CACHE_TTL_SEC = 300.0


def _run(cmd: list[str], timeout: float = 12) -> str:
    try:
        from tools.win_subprocess import run_hidden

        p = run_hidden(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if p.returncode != 0:
            return ""
        return (p.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _ps(script: str, timeout: float = 15) -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return ""
    return _run(
        [exe, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
        timeout=timeout,
    )


def _gb(nbytes: float | int | None) -> float | None:
    if nbytes is None:
        return None
    try:
        n = float(nbytes)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n < 256:
        return round(n, 2)
    if n < 1024 * 1024:
        return round(n / 1024.0, 2)
    if n < 1024**3:
        return round(n / (1024**2), 2)
    return round(n / (1024**3), 2)


def _vendor_from_name(name: str) -> str:
    low = name.lower()
    if any(x in low for x in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla", "titan")):
        return "nvidia"
    if any(x in low for x in ("amd", "radeon", "rx ", "vega", "instinct")):
        return "amd"
    if any(x in low for x in ("intel", "arc ", "iris", "uhd graphics", "xe graphics")):
        return "intel"
    if any(x in low for x in ("adreno", "qualcomm", "snapdragon")):
        return "qualcomm"
    if any(x in low for x in ("apple", "m1", "m2", "m3", "m4", "m5")):
        return "apple"
    return "unknown"


def _is_shared_gpu(vendor: str, name: str, mem_gb: float | None) -> bool:
    low = name.lower()
    if vendor in ("qualcomm", "apple"):
        return True
    if vendor == "intel" and not re.search(r"\barc\b", low):
        return True
    if "iris" in low or "uhd" in low or "integrated" in low:
        return True
    if mem_gb is None:
        return vendor != "nvidia"
    return False


def _nvidia_gpus() -> list[dict[str, Any]]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    raw = _run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            mem_mib = float(parts[1])
        except ValueError:
            continue
        out.append(
            {
                "kind": "gpu",
                "vendor": "nvidia",
                "name": parts[0],
                "memory_gb": round(mem_mib / 1024.0, 2),
                "memory_shared": False,
                "source": "nvidia-smi",
            }
        )
    return out


def _amd_rocm_gpus() -> list[dict[str, Any]]:
    smi = shutil.which("rocm-smi")
    if not smi:
        return []
    raw = _run([smi, "--showmeminfo", "vram", "--json"])
    if not raw:
        raw = _run([smi, "--showproductname", "--showmeminfo", "vram"])
    out: list[dict[str, Any]] = []
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            for key, card in data.items():
                if not isinstance(card, dict):
                    continue
                name = str(card.get("Card Series") or card.get("Device Name") or key)
                mem = None
                for mk in ("VRAM Total Memory (B)", "vram"):
                    if mk in card:
                        mem = _gb(card[mk])
                        break
                out.append(
                    {
                        "kind": "gpu",
                        "vendor": "amd",
                        "name": name,
                        "memory_gb": mem,
                        "memory_shared": mem is None,
                        "source": "rocm-smi",
                    }
                )
    except json.JSONDecodeError:
        name = "AMD GPU"
        mem = None
        m = re.search(r"(\d+)\s*MiB", raw, re.I)
        if m:
            mem = round(float(m.group(1)) / 1024.0, 2)
        if "vram" in raw.lower() or "amd" in raw.lower():
            out.append(
                {
                    "kind": "gpu",
                    "vendor": "amd",
                    "name": name,
                    "memory_gb": mem,
                    "memory_shared": mem is None,
                    "source": "rocm-smi",
                }
            )
    return out


def _win_ram_ctypes() -> float | None:
    """RAM via kernel32 — no console, no PowerShell."""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return _gb(stat.ullTotalPhys)
    except Exception:
        return None
    return None


def _system_ram_gb() -> float | None:
    if sys.platform == "win32":
        ram = _win_ram_ctypes()
        if ram:
            return ram
        raw = _ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
        try:
            return _gb(float(raw.splitlines()[0].strip()))
        except (IndexError, ValueError):
            wmic = _run(["wmic", "computersystem", "get", "TotalPhysicalMemory", "/value"])
            m = re.search(r"TotalPhysicalMemory=(\d+)", wmic)
            return _gb(float(m.group(1))) if m else None
    if sys.platform == "darwin":
        raw = _run(["sysctl", "-n", "hw.memsize"])
        try:
            return _gb(float(raw.splitlines()[0].strip()))
        except (IndexError, ValueError):
            return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page = os.sysconf("SC_PAGE_SIZE")
        return _gb(pages * page)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                kb = float(line.split()[1])
                return round(kb / (1024 * 1024), 2)
    except OSError:
        return None
    return None


def _win_video_controllers() -> list[dict[str, Any]]:
    raw = _ps(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM, PNPDeviceID | ConvertTo-Json -Compress"
    )
    if not raw:
        raw = _run(["wmic", "path", "win32_videocontroller", "get", "Name,AdapterRAM", "/format:csv"])
        out: list[dict[str, Any]] = []
        for line in raw.splitlines()[1:]:
            cols = [c.strip() for c in line.split(",")]
            if len(cols) < 3:
                continue
            try:
                ram = float(cols[-2]) if cols[-2].isdigit() else None
            except ValueError:
                ram = None
            name = cols[-1]
            if not name:
                continue
            vendor = _vendor_from_name(name)
            mem = _gb(ram) if ram and ram not in (0, 4294967295) else None
            out.append(
                {
                    "kind": "gpu",
                    "vendor": vendor,
                    "name": name,
                    "memory_gb": mem,
                    "memory_shared": _is_shared_gpu(vendor, name, mem),
                    "source": "wmic",
                }
            )
        return out
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    rows = data if isinstance(data, list) else [data]
    out = []
    for row in rows:
        name = str(row.get("Name") or "").strip()
        if not name:
            continue
        low = name.lower()
        if "microsoft basic" in low or "remote desktop" in low:
            continue
        ram = row.get("AdapterRAM")
        mem = _gb(ram) if isinstance(ram, (int, float)) and ram not in (0, 4294967295) else None
        vendor = _vendor_from_name(name)
        out.append(
            {
                "kind": "gpu",
                "vendor": vendor,
                "name": name,
                "memory_gb": mem,
                "memory_shared": _is_shared_gpu(vendor, name, mem),
                "source": "Win32_VideoController",
                "pnp": str(row.get("PNPDeviceID") or ""),
            }
        )
    return out


def _win_npus() -> list[dict[str, Any]]:
    raw = _ps(
        "$devs = Get-CimInstance Win32_PnPEntity | Where-Object { "
        "$_.Name -match 'NPU|Neural Processing|Hexagon|AI Boost|Intel\\(R\\) AI Boost|"
        "Snapdragon.*NPU|Qualcomm.*NPU|Copilot\\+ PC NPU|Ryzen AI' }; "
        "if (-not $devs) { '[]' } else { $devs | Select-Object Name, PNPDeviceID, Manufacturer | "
        "ConvertTo-Json -Compress }"
    )
    if not raw or raw == "[]":
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    rows = data if isinstance(data, list) else [data]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("Name") or "").strip()
        if not name or name.lower() in seen:
            continue
        low = name.lower()
        if any(
            x in low
            for x in (
                "input configuration",
                "keyboard",
                "mouse",
                "touchpad",
                "hid-compliant",
                "hid compliant",
            )
        ):
            continue
        if not any(
            x in low
            for x in ("npu", "hexagon", "neural processing", "ai boost", "ryzen ai")
        ):
            continue
        seen.add(name.lower())
        out.append(
            {
                "kind": "npu",
                "vendor": _vendor_from_name(name),
                "name": name,
                "memory_gb": None,
                "memory_shared": True,
                "source": "Win32_PnPEntity",
                "pnp": str(row.get("PNPDeviceID") or ""),
            }
        )
    return out


def _linux_sysfs_gpus() -> list[dict[str, Any]]:
    try:
        cards = sorted(Path("/sys/class/drm").glob("card[0-9]*"))
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for card in cards:
        if "-" in card.name:
            continue
        name = card.name
        vendor = "unknown"
        mem = None
        try:
            v = (card / "device" / "vendor").read_text(encoding="utf-8").strip()
            vendor = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel", "0x5143": "qualcomm"}.get(v, vendor)
        except OSError:
            pass
        try:
            uevent = (card / "device" / "uevent").read_text(encoding="utf-8")
            m = re.search(r"DRIVER=(\S+)", uevent)
            if m and name == card.name:
                name = f"{card.name} ({m.group(1)})"
        except OSError:
            pass
        for cand in (
            card / "device" / "mem_info_vram_total",
            card / "device" / "gpu_mem_total",
        ):
            try:
                mem = _gb(int(cand.read_text(encoding="utf-8").strip()))
                break
            except (OSError, ValueError):
                continue
        out.append(
            {
                "kind": "gpu",
                "vendor": vendor,
                "name": name,
                "memory_gb": mem,
                "memory_shared": _is_shared_gpu(vendor, name, mem),
                "source": "sysfs",
            }
        )
    return out


def _linux_npus() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        accel = Path("/sys/class/accel")
        if accel.is_dir():
            for p in accel.iterdir():
                out.append(
                    {
                        "kind": "npu",
                        "vendor": "unknown",
                        "name": p.name,
                        "memory_gb": None,
                        "memory_shared": True,
                        "source": "sysfs-accel",
                    }
                )
    except OSError:
        pass
    return out


def _macos_displays() -> list[dict[str, Any]]:
    raw = _run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=20)
    out: list[dict[str, Any]] = []
    if raw:
        try:
            data = json.loads(raw)
            for row in data.get("SPDisplaysDataType") or []:
                name = str(row.get("sppci_model") or row.get("_name") or "GPU")
                vram = row.get("spdisplays_vram") or row.get("spdisplays_vram_shared")
                mem = None
                if isinstance(vram, (int, float)):
                    mem = _gb(vram)
                elif isinstance(vram, str):
                    m = re.search(r"([\d.]+)\s*(GB|MB)", vram, re.I)
                    if m:
                        n = float(m.group(1))
                        mem = n if m.group(2).upper() == "GB" else round(n / 1024.0, 2)
                vendor = _vendor_from_name(name)
                shared = "shared" in str(vram).lower() or _is_shared_gpu(vendor, name, mem)
                out.append(
                    {
                        "kind": "gpu",
                        "vendor": vendor,
                        "name": name,
                        "memory_gb": mem,
                        "memory_shared": shared,
                        "source": "system_profiler",
                    }
                )
        except json.JSONDecodeError:
            pass
    chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    if chip and re.search(r"\bM[1-5]\b", chip):
        if not any(d.get("vendor") == "apple" for d in out):
            out.append(
                {
                    "kind": "gpu",
                    "vendor": "apple",
                    "name": f"{chip} GPU",
                    "memory_gb": None,
                    "memory_shared": True,
                    "source": "sysctl",
                }
            )
        out.append(
            {
                "kind": "npu",
                "vendor": "apple",
                "name": "Apple Neural Engine",
                "memory_gb": None,
                "memory_shared": True,
                "source": "sysctl",
            }
        )
    return out


def _dedupe(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for d in devices:
        key = re.sub(r"\s+", " ", f"{d.get('kind')}|{d.get('name')}".lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _usable_gb(devices: list[dict[str, Any]], ram_gb: float | None) -> tuple[float, str]:
    dedicated = [d for d in devices if d.get("memory_gb") and not d.get("memory_shared")]
    if dedicated:
        return max(float(d["memory_gb"]) for d in dedicated), "dedicated_gpu"
    if ram_gb:
        unified = any(d.get("memory_shared") or d.get("vendor") in ("apple", "qualcomm") for d in devices)
        if unified or any(d.get("kind") in ("gpu", "npu") for d in devices):
            if ram_gb >= 64:
                return round(min(ram_gb * 0.3, 24.0), 2), "unified_or_system_ram"
            if ram_gb >= 48:
                return round(min(ram_gb * 0.3, 14.0), 2), "unified_or_system_ram"
            return round(min(ram_gb * 0.28, 8.5), 2), "unified_or_system_ram"
        return round(min(ram_gb * 0.22, 6.0), 2), "cpu_ram"
    return 2.0, "fallback"


def _runtime_hint(devices: list[dict[str, Any]]) -> str:
    """Which llama.cpp flavor this machine should download."""
    names = " ".join(str(d.get("name") or "") for d in devices).lower()
    vendors = {str(d.get("vendor") or "") for d in devices}
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        if arm and ("adreno" in names or "qualcomm" in vendors):
            return "win-opencl-adreno-arm64"
        if arm:
            return "win-cpu-arm64"
        if any(d.get("vendor") == "nvidia" and not d.get("memory_shared") for d in devices):
            return "win-cuda-12.4-x64"
        if "amd" in vendors:
            return "win-vulkan-x64"
        return "win-cpu-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if arm else "macos-x64"
    if arm:
        return "ubuntu-cpu-arm64"
    if any(d.get("vendor") == "nvidia" and not d.get("memory_shared") for d in devices):
        return "ubuntu-cuda"
    return "ubuntu-cpu-x64"


def _pending_hardware() -> dict[str, Any]:
    ram = _win_ram_ctypes() if sys.platform == "win32" else None
    return {
        "os": f"{platform.system()} {platform.release()}".strip(),
        "arch": platform.machine(),
        "cpu": platform.processor() or None,
        "system_ram_gb": ram,
        "devices": [],
        "has_gpu": False,
        "has_npu": False,
        "usable_memory_gb": ram,
        "usable_from": "pending",
        "accelerator_count": 0,
        "recommended_runtime": None,
        "probe": "pending",
    }


def _read_disk_cache() -> dict[str, Any] | None:
    try:
        from config import data_root

        path = data_root() / "hardware.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data else None
    except Exception:
        return None


def hardware_snapshot(*, refresh: bool = True) -> dict[str, Any]:
    """Return last GPU/RAM probe immediately. Refresh runs in a hidden background thread."""
    global _CACHE, _CACHE_AT
    if _CACHE is None:
        disk = _read_disk_cache()
        if disk:
            _CACHE = disk
            _CACHE_AT = time.time()
    if refresh:
        stale = _CACHE is None or (time.time() - _CACHE_AT) >= _CACHE_TTL_SEC
        pending = bool(_CACHE and _CACHE.get("probe") == "pending")
        if stale or pending:
            schedule_hardware_probe()
    return _CACHE or _pending_hardware()


def schedule_hardware_probe() -> None:
    """Start one silent probe thread. Never blocks the caller."""
    global _PROBE_RUNNING
    with _PROBE_LOCK:
        if _PROBE_RUNNING:
            return
        _PROBE_RUNNING = True

    def _run() -> None:
        global _PROBE_RUNNING
        try:
            detect_hardware(force=True)
        except Exception:
            pass
        finally:
            with _PROBE_LOCK:
                _PROBE_RUNNING = False

    threading.Thread(target=_run, name="jarvis-hw-probe", daemon=True).start()


def detect_hardware(*, force: bool = False) -> dict[str, Any]:
    """Probe this machine. Call from a background thread — this can spawn nvidia-smi / CIM."""
    global _CACHE, _CACHE_AT
    if not force:
        snap = hardware_snapshot(refresh=True)
        if snap.get("probe") != "pending" and _CACHE and (time.time() - _CACHE_AT) < _CACHE_TTL_SEC:
            return snap
    with _DETECT_LOCK:
        if _CACHE and not force and (time.time() - _CACHE_AT) < _CACHE_TTL_SEC and _CACHE.get("probe") != "pending":
            return _CACHE

        devices: list[dict[str, Any]] = []
        devices.extend(_nvidia_gpus())
        devices.extend(_amd_rocm_gpus())
        nvidia_names = {re.sub(r"\s+", " ", d["name"].lower()) for d in devices if d.get("vendor") == "nvidia"}

        if sys.platform == "win32":
            for d in _win_video_controllers():
                key = re.sub(r"\s+", " ", d["name"].lower())
                if any(n in key or key in n for n in nvidia_names):
                    continue
                devices.append(d)
            devices.extend(_win_npus())
        elif sys.platform == "darwin":
            devices.extend(_macos_displays())
        else:
            if not any(d.get("source") == "nvidia-smi" for d in devices):
                devices.extend(_linux_sysfs_gpus())
            devices.extend(_linux_npus())

        devices = _dedupe(devices)
        ram_gb = _system_ram_gb()
        usable_gb, usable_from = _usable_gb(devices, ram_gb)
        for d in devices:
            if d.get("memory_gb") is None and d.get("memory_shared") and ram_gb:
                d["memory_gb_estimate"] = round(min(8.0, ram_gb * 0.25), 2)

        result = {
            "os": f"{platform.system()} {platform.release()}".strip(),
            "arch": platform.machine(),
            "cpu": platform.processor() or None,
            "system_ram_gb": ram_gb,
            "devices": devices,
            "has_gpu": any(d.get("kind") == "gpu" for d in devices),
            "has_npu": any(d.get("kind") == "npu" for d in devices),
            "usable_memory_gb": usable_gb,
            "usable_from": usable_from,
            "accelerator_count": sum(1 for d in devices if d.get("kind") in ("gpu", "npu")),
            "recommended_runtime": _runtime_hint(devices),
        }
        _CACHE = result
        _CACHE_AT = time.time()
        try:
            from config import data_root

            path = data_root() / "hardware.json"
            path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception:
            pass
        return result
