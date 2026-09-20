"""Recommend, download (Hugging Face), and load local open-source chat models."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from agents.hardware import hardware_snapshot
from config import data_root

# Single-file GGUF where possible (bartowski / HuggingFaceTB). min_vram includes KV + runtime slop.
CATALOG: list[dict[str, Any]] = [
    {
        "id": "smollm2-360m-q8",
        "name": "SmolLM2 360M Instruct (Q8)",
        "license": "Apache-2.0",
        "params": "360M",
        "quant": "Q8_0",
        "min_vram_gb": 1.0,
        "size_gb": 0.4,
        "repo_id": "HuggingFaceTB/SmolLM2-360M-Instruct-GGUF",
        "filename_hints": ["q8_0", "Q8_0"],
        "transformers_id": "HuggingFaceTB/SmolLM2-360M-Instruct",
    },
    {
        "id": "smollm2-1.7b-q4",
        "name": "SmolLM2 1.7B Instruct (Q4)",
        "license": "Apache-2.0",
        "params": "1.7B",
        "quant": "Q4_K_M",
        "min_vram_gb": 2.4,
        "size_gb": 1.1,
        "repo_id": "HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF",
        "filename_hints": ["q4_k_m", "Q4_K_M"],
        "transformers_id": "HuggingFaceTB/SmolLM2-1.7B-Instruct",
    },
    {
        "id": "qwen25-1.5b-q4",
        "name": "Qwen2.5 1.5B Instruct (Q4)",
        "license": "Apache-2.0",
        "params": "1.5B",
        "quant": "Q4_K_M",
        "min_vram_gb": 2.2,
        "size_gb": 1.1,
        "repo_id": "bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        "filename_hints": ["Q4_K_M"],
        "transformers_id": "Qwen/Qwen2.5-1.5B-Instruct",
    },
    {
        "id": "qwen25-3b-q4",
        "name": "Qwen2.5 3B Instruct (Q4)",
        "license": "Apache-2.0",
        "params": "3B",
        "quant": "Q4_K_M",
        "min_vram_gb": 3.6,
        "size_gb": 2.0,
        "repo_id": "bartowski/Qwen2.5-3B-Instruct-GGUF",
        "filename_hints": ["Q4_K_M"],
        "transformers_id": "Qwen/Qwen2.5-3B-Instruct",
    },
    {
        "id": "phi35-mini-q4",
        "name": "Phi-3.5 Mini Instruct (Q4)",
        "license": "MIT",
        "params": "3.8B",
        "quant": "Q4_K_M",
        "min_vram_gb": 4.0,
        "size_gb": 2.5,
        "repo_id": "bartowski/Phi-3.5-mini-instruct-GGUF",
        "filename_hints": ["Q4_K_M"],
        "transformers_id": "microsoft/Phi-3.5-mini-instruct",
    },
    {
        "id": "qwen25-7b-q4",
        "name": "Qwen2.5 7B Instruct (Q4)",
        "license": "Apache-2.0",
        "params": "7B",
        "quant": "Q4_K_M",
        "min_vram_gb": 6.5,
        "size_gb": 4.7,
        "repo_id": "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "filename_hints": ["Q4_K_M"],
        "transformers_id": None,
    },
    {
        "id": "qwen25-14b-q4",
        "name": "Qwen2.5 14B Instruct (Q4)",
        "license": "Apache-2.0",
        "params": "14B",
        "quant": "Q4_K_M",
        "min_vram_gb": 11.0,
        "size_gb": 9.0,
        "repo_id": "bartowski/Qwen2.5-14B-Instruct-GGUF",
        "filename_hints": ["Q4_K_M"],
        "transformers_id": None,
    },
    {
        "id": "qwen25-32b-q4",
        "name": "Qwen2.5 32B Instruct (Q4)",
        "license": "Apache-2.0",
        "params": "32B",
        "quant": "Q4_K_M",
        "min_vram_gb": 22.0,
        "size_gb": 20.0,
        "repo_id": "bartowski/Qwen2.5-32B-Instruct-GGUF",
        "filename_hints": ["Q4_K_M"],
        "transformers_id": None,
    },
]


_LOCK = threading.Lock()
_RUNTIME: dict[str, Any] = {
    "llm": None,
    "backend": None,
    "model_id": None,
    "chat_model": "local",
}
_JOB: dict[str, Any] = {
    "status": "idle",
    "progress": 0,
    "message": "",
    "model_id": "",
    "error": "",
}


def models_dir() -> Path:
    p = data_root() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def hf_cache_dir() -> Path:
    p = models_dir() / "hf-cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def catalog_entry(model_id: str) -> dict[str, Any] | None:
    mid = (model_id or "").strip()
    for e in CATALOG:
        if e["id"] == mid:
            return e
    return None


def llama_cpp_available() -> bool:
    try:
        import llama_cpp  # noqa: F401

        return True
    except Exception:
        return False


def llama_server_available() -> bool:
    from agents.llama_cpp_bin import available

    return available()


def transformers_available() -> bool:
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401

        return True
    except Exception:
        return False


def _index_path() -> Path:
    return models_dir() / "index.json"


def _read_index() -> dict[str, Any]:
    p = _index_path()
    if not p.exists():
        return {"installed": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("installed", {})
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"installed": {}}


def _write_index(data: dict[str, Any]) -> None:
    _index_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def installed_record(model_id: str) -> dict[str, Any] | None:
    rec = _read_index().get("installed", {}).get(model_id)
    return rec if isinstance(rec, dict) else None


def is_installed(model_id: str) -> bool:
    rec = installed_record(model_id)
    if not rec:
        return False
    path = rec.get("path")
    return bool(path and Path(path).exists())


def loaded_model_id() -> Optional[str]:
    return _RUNTIME.get("model_id")


def job_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_JOB)


def _set_job(**kwargs: Any) -> None:
    with _LOCK:
        _JOB.update(kwargs)


def _usable_for_device(device: dict[str, Any], hw: dict[str, Any]) -> float:
    if device.get("memory_gb") and not device.get("memory_shared"):
        return float(device["memory_gb"])
    if device.get("memory_gb_estimate"):
        return float(device["memory_gb_estimate"])
    return float(hw.get("usable_memory_gb") or 2.0)


def _best_for_gb(usable_gb: float) -> dict[str, Any]:
    fits = [e for e in CATALOG if float(e["min_vram_gb"]) <= usable_gb + 0.05]
    if not fits:
        return CATALOG[0]
    return max(fits, key=lambda e: float(e["min_vram_gb"]))


def recommend(hw: dict[str, Any] | None = None) -> dict[str, Any]:
    hw = hw or hardware_snapshot()
    usable = float(hw.get("usable_memory_gb") or 2.0)
    suggested = _best_for_gb(usable)
    per_device: list[dict[str, Any]] = []
    for d in hw.get("devices") or []:
        ug = _usable_for_device(d, hw)
        best = _best_for_gb(ug)
        per_device.append(
            {
                **d,
                "usable_gb": ug,
                "suggested_model_id": best["id"],
                "suggested_name": best["name"],
            }
        )
    if not per_device:
        per_device.append(
            {
                "kind": "cpu",
                "vendor": "cpu",
                "name": "System RAM (CPU)",
                "memory_gb": hw.get("system_ram_gb"),
                "memory_shared": True,
                "usable_gb": usable,
                "suggested_model_id": suggested["id"],
                "suggested_name": suggested["name"],
                "source": "fallback",
            }
        )
    return {
        "hardware": hw,
        "suggested_model_id": suggested["id"],
        "suggested_name": suggested["name"],
        "devices": per_device,
        "backends": {
            "llama_cpp": llama_cpp_available(),
            "llama_server": llama_server_available(),
            "transformers": transformers_available(),
        },
        "catalog": [_public_entry(e, usable) for e in CATALOG],
        "loaded_model_id": loaded_model_id(),
        "job": job_status(),
    }


def _public_entry(e: dict[str, Any], usable_gb: float) -> dict[str, Any]:
    return {
        "id": e["id"],
        "name": e["name"],
        "license": e["license"],
        "params": e["params"],
        "quant": e["quant"],
        "min_vram_gb": e["min_vram_gb"],
        "size_gb": e["size_gb"],
        "repo_id": e["repo_id"],
        "huggingface_url": f"https://huggingface.co/{e['repo_id']}",
        "fits": float(e["min_vram_gb"]) <= usable_gb + 0.05,
        "installed": is_installed(e["id"]),
        "loaded": loaded_model_id() == e["id"],
        "recommended": False,
    }


def _mark_recommended(payload: dict[str, Any]) -> dict[str, Any]:
    sid = payload.get("suggested_model_id")
    for e in payload.get("catalog") or []:
        e["recommended"] = e.get("id") == sid
    return payload


def public_status() -> dict[str, Any]:
    return _mark_recommended(recommend())


def _pick_gguf_file(repo_id: str, hints: list[str], token: Optional[str]) -> str:
    from huggingface_hub import list_repo_files

    files = list_repo_files(repo_id, token=token)
    ggufs = [f for f in files if f.lower().endswith(".gguf") and "00001-of" not in f.lower()]
    if not ggufs:
        ggufs = [f for f in files if f.lower().endswith(".gguf")]
    if not ggufs:
        raise FileNotFoundError(f"No GGUF files in {repo_id}")
    for hint in hints:
        h = hint.lower()
        matches = [f for f in ggufs if h in f.lower() and "0000" not in Path(f).name.lower()]
        if matches:
            matches.sort(key=len)
            return matches[0]
    ggufs.sort(key=len)
    return ggufs[0]


def _hf_token() -> Optional[str]:
    import os

    t = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    return t or None


def _download_gguf(entry: dict[str, Any]) -> str:
    from huggingface_hub import hf_hub_download

    token = _hf_token()
    filename = _pick_gguf_file(entry["repo_id"], entry.get("filename_hints") or [], token)
    _set_job(message=f"Downloading {filename} from {entry['repo_id']}", progress=2)
    path = hf_hub_download(
        repo_id=entry["repo_id"],
        filename=filename,
        cache_dir=str(hf_cache_dir()),
        token=token,
    )
    return path


def _download_transformers(entry: dict[str, Any]) -> str:
    from huggingface_hub import snapshot_download

    tid = entry.get("transformers_id")
    if not tid:
        raise ValueError("No transformers repo for this model")
    dest = models_dir() / "transformers" / entry["id"]
    dest.mkdir(parents=True, exist_ok=True)
    _set_job(message=f"Downloading {tid} (safetensors)", progress=2)
    snapshot_download(
        repo_id=tid,
        cache_dir=str(hf_cache_dir()),
        local_dir=str(dest),
        token=_hf_token(),
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "*.model",
            "tokenizer.*",
            "*.txt",
            "*.jinja",
        ],
    )
    return str(dest)


def _choose_download_kind(entry: dict[str, Any]) -> str:
    if llama_cpp_available():
        return "gguf"
    if entry.get("transformers_id") and float(entry["min_vram_gb"]) <= 6.5:
        return "transformers"
    return "gguf"


def download_model(model_id: str) -> dict[str, Any]:
    entry = catalog_entry(model_id)
    if not entry:
        raise ValueError(f"Unknown model: {model_id}")
    with _LOCK:
        if _JOB.get("status") in ("downloading", "loading"):
            raise RuntimeError("A download or load is already running.")
        _JOB.update(
            {
                "status": "downloading",
                "progress": 1,
                "message": f"Starting {entry['name']}",
                "model_id": entry["id"],
                "error": "",
            }
        )
    t = threading.Thread(target=_download_worker, args=(entry,), daemon=True)
    t.start()
    return job_status()


def _download_worker(entry: dict[str, Any]) -> None:
    try:
        kind = _choose_download_kind(entry)
        if kind == "transformers":
            path = _download_transformers(entry)
        else:
            path = _download_gguf(entry)
        idx = _read_index()
        idx["installed"][entry["id"]] = {
            "id": entry["id"],
            "path": path,
            "kind": kind,
            "repo_id": entry["repo_id"] if kind == "gguf" else entry.get("transformers_id"),
            "downloaded_at": time.time(),
        }
        _write_index(idx)
        _set_job(status="loading", progress=85, message="Download finished — preparing llama.cpp")
        try:
            from agents.llama_cpp_bin import ensure_binary

            ensure_binary(lambda m: _set_job(message=m, progress=90))
        except Exception as e:
            _set_job(message=f"Runtime download skipped: {e}")
        load_model(entry["id"])
        _set_job(
            status="ready",
            progress=100,
            message=f"Ready: {entry['name']}",
            model_id=entry["id"],
            error="",
        )
    except Exception as e:
        _set_job(status="error", message=str(e), error=str(e))


def _n_gpu_layers() -> int:
    if shutil_which_nvidia():
        return -1
    return 0


def shutil_which_nvidia() -> bool:
    import shutil

    return bool(shutil.which("nvidia-smi"))


def load_model(model_id: str) -> dict[str, Any]:
    entry = catalog_entry(model_id)
    if not entry:
        raise ValueError(f"Unknown model: {model_id}")
    rec = installed_record(model_id)
    if not rec or not Path(rec.get("path") or "").exists():
        raise FileNotFoundError(f"{entry['name']} is not downloaded yet.")
    path = rec["path"]
    kind = rec.get("kind") or "gguf"
    if kind == "gguf":
        if llama_cpp_available():
            from llama_cpp import Llama

            llm = Llama(
                model_path=path,
                n_ctx=4096,
                n_gpu_layers=_n_gpu_layers(),
                verbose=False,
            )
            with _LOCK:
                old = _RUNTIME.get("llm")
                _RUNTIME.update(
                    {
                        "llm": llm,
                        "backend": "llama_cpp",
                        "model_id": entry["id"],
                        "chat_model": entry["name"],
                    }
                )
            _close_old(old)
        else:
            from agents.llama_cpp_bin import start_server

            _set_job(message="Starting local llama.cpp server")
            base = start_server(path)
            with _LOCK:
                old = _RUNTIME.get("llm")
                _RUNTIME.update(
                    {
                        "llm": {"base_url": base},
                        "backend": "llama_server",
                        "model_id": entry["id"],
                        "chat_model": entry["name"],
                    }
                )
            _close_old(old)
    else:
        if not transformers_available():
            raise RuntimeError(
                "Weights are on disk, but transformers/torch is not installed. "
                "Run: pip install transformers torch"
            )
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tok = AutoTokenizer.from_pretrained(path, trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(
            path,
            trust_remote_code=False,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
        model.eval()
        with _LOCK:
            old = _RUNTIME.get("llm")
            _RUNTIME.update(
                {
                    "llm": {"model": model, "tok": tok},
                    "backend": "transformers",
                    "model_id": entry["id"],
                    "chat_model": entry["name"],
                }
            )
        _close_old(old)
    try:
        from agents.models.local_client import refresh_chat_model_label

        refresh_chat_model_label()
    except Exception:
        pass
    return {
        "ok": True,
        "model_id": entry["id"],
        "backend": _RUNTIME["backend"],
        "name": entry["name"],
    }


def _close_old(old: Any) -> None:
    if old is None:
        return
    try:
        if hasattr(old, "close"):
            old.close()
        elif isinstance(old, dict) and old.get("base_url"):
            from agents.llama_cpp_bin import stop_server

            stop_server()
        elif isinstance(old, dict) and old.get("model") is not None:
            del old["model"]
    except Exception:
        pass


def ensure_ready(model_id: Optional[str] = None) -> None:
    mid = (model_id or loaded_model_id() or "").strip()
    if mid and loaded_model_id() == mid and _RUNTIME.get("llm") is not None:
        return
    if not mid:
        mid = public_status().get("suggested_model_id") or CATALOG[0]["id"]
    if not is_installed(mid):
        raise RuntimeError(f"Local model {mid} is not downloaded. Use Settings → Download.")
    load_model(mid)


def generate_chat(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1024,
    stream: bool = False,
):
    if _RUNTIME.get("llm") is None:
        ensure_ready()
    backend = _RUNTIME.get("backend")
    llm = _RUNTIME.get("llm")
    clean = []
    for m in messages:
        role = (m.get("role") or "user").strip().lower()
        if role not in ("system", "user", "assistant"):
            role = "user"
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(p.get("text") or "") for p in content if isinstance(p, dict)
            )
        clean.append({"role": role, "content": str(content or "").strip() or " "})

    if backend == "llama_server":
        from openai import OpenAI

        client = OpenAI(api_key="local", base_url=llm["base_url"])
        if stream:
            stream_obj = client.chat.completions.create(
                model="local",
                messages=clean,
                max_tokens=max_tokens,
                stream=True,
            )
            for chunk in stream_obj:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
            return
        resp = client.chat.completions.create(
            model="local",
            messages=clean,
            max_tokens=max_tokens,
        )
        yield (resp.choices[0].message.content or "").strip()
        return

    if backend == "llama_cpp":
        if stream:
            it = llm.create_chat_completion(
                messages=clean, max_tokens=max_tokens, stream=True
            )
            for chunk in it:
                delta = (
                    ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    or ""
                )
                if delta:
                    yield delta
            return
        out = llm.create_chat_completion(messages=clean, max_tokens=max_tokens)
        text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        yield text
        return

    if backend == "transformers":
        import torch

        tok = llm["tok"]
        model = llm["model"]
        prompt = tok.apply_chat_template(
            clean, tokenize=False, add_generation_prompt=True
        )
        inputs = tok(prompt, return_tensors="pt")
        with torch.no_grad():
            ids = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=False,
                pad_token_id=tok.eos_token_id,
            )
        gen = ids[0][inputs["input_ids"].shape[1] :]
        text = tok.decode(gen, skip_special_tokens=True)
        if stream:
            yield text
        else:
            yield text
        return

    raise RuntimeError("No local model is loaded.")
