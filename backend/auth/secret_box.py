"""Local-at-rest encryption for OAuth tokens.

Windows uses DPAPI (same user). Other platforms use a SHA-256 stream keyed
from the install API token. Combined with a 0600 store file.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
from typing import Any


_MAGIC = b"ADA1"


def _xor_keystream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    counter = 0
    i = 0
    while i < len(data):
        block = hashlib.sha256(key + nonce + struct.pack(">I", counter)).digest()
        take = min(len(block), len(data) - i)
        for j in range(take):
            out[i + j] = data[i + j] ^ block[j]
        i += take
        counter += 1
    return bytes(out)


def _software_key() -> bytes:
    try:
        from auth.local_token import get_or_create_token

        material = get_or_create_token().encode("utf-8")
    except Exception:
        material = (os.environ.get("ADA_API_TOKEN") or "ada-local").encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", material, b"ada-oauth-v1", 120_000, dklen=32)


def _dpapi_protect(data: bytes) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            "AdaOAuth",
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def _dpapi_unprotect(data: bytes) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def seal_bytes(plaintext: bytes) -> dict[str, Any]:
    dpapi = _dpapi_protect(plaintext)
    if dpapi is not None:
        return {"v": 2, "alg": "dpapi", "blob": base64.b64encode(dpapi).decode("ascii")}
    key = _software_key()
    nonce = os.urandom(16)
    ct = _xor_keystream(plaintext, key, nonce)
    mac = hmac.new(key, _MAGIC + nonce + ct, hashlib.sha256).digest()
    packed = _MAGIC + nonce + mac + ct
    return {"v": 2, "alg": "hmac-sha256", "blob": base64.b64encode(packed).decode("ascii")}


def open_bytes(obj: dict[str, Any]) -> bytes | None:
    alg = str(obj.get("alg") or "")
    try:
        raw = base64.b64decode(str(obj.get("blob") or ""), validate=True)
    except Exception:
        return None
    if alg == "dpapi":
        return _dpapi_unprotect(raw)
    if alg != "hmac-sha256" or len(raw) < 4 + 16 + 32:
        return None
    if raw[:4] != _MAGIC:
        return None
    nonce = raw[4:20]
    mac = raw[20:52]
    ct = raw[52:]
    key = _software_key()
    expect = hmac.new(key, _MAGIC + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expect):
        return None
    return _xor_keystream(ct, key, nonce)


def seal_json(data: dict[str, Any]) -> dict[str, Any]:
    return seal_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def open_json(obj: Any) -> dict[str, Any] | None:
    if not isinstance(obj, dict) or "blob" not in obj:
        return None
    raw = open_bytes(obj)
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None
