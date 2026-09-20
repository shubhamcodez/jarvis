"""Screen capture, crops, DPI mapping, and cheap visual hashes."""
from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from typing import Optional

try:
    import mss
    import mss.tools

    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class ScreenShot:
    """One capture in screenshot-pixel space, plus mapping to the OS mouse."""

    png: bytes
    width: int
    height: int
    mouse_scale_x: float = 1.0
    mouse_scale_y: float = 1.0

    @property
    def b64(self) -> str:
        return base64.b64encode(self.png).decode("ascii")

    def to_mouse(self, x: float, y: float) -> tuple[int, int]:
        mx = int(round(float(x) * self.mouse_scale_x))
        my = int(round(float(y) * self.mouse_scale_y))
        return mx, my


def _mouse_size() -> tuple[int, int]:
    try:
        import pyautogui

        w, h = pyautogui.size()
        return int(w), int(h)
    except Exception:
        return 0, 0


def capture_screen() -> ScreenShot:
    if not HAS_MSS:
        raise RuntimeError("mss not installed. pip install mss")
    with mss.mss() as sct:
        mon = sct.monitors[0]
        width = int(mon["width"])
        height = int(mon["height"])
        shot = sct.grab(mon)
        png = mss.tools.to_png(shot.rgb, shot.size)
    mw, mh = _mouse_size()
    sx = (mw / width) if width and mw else 1.0
    sy = (mh / height) if height and mh else 1.0
    return ScreenShot(png=png, width=width, height=height, mouse_scale_x=sx, mouse_scale_y=sy)


def decode_png(png: bytes) -> "Image.Image":
    if not HAS_PIL:
        raise RuntimeError("Pillow not installed")
    return Image.open(io.BytesIO(png)).convert("RGB")


def encode_png(img: "Image.Image") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def crop_rect(shot: ScreenShot, x: int, y: int, w: int, h: int) -> bytes:
    img = decode_png(shot.png)
    x = max(0, min(int(x), shot.width - 1))
    y = max(0, min(int(y), shot.height - 1))
    w = max(8, min(int(w), shot.width - x))
    h = max(8, min(int(h), shot.height - y))
    return encode_png(img.crop((x, y, x + w, y + h)))


def resize_for_vision(png: bytes, max_side: int = 1280) -> tuple[bytes, int, int, float, float]:
    """Downscale for the VLM. Returns (png, w, h, scale_from_model_x, scale_from_model_y)."""
    img = decode_png(png)
    w, h = img.size
    if max(w, h) <= max_side:
        return png, w, h, 1.0, 1.0
    if w >= h:
        nw, nh = max_side, max(1, int(round(h * max_side / w)))
    else:
        nh, nw = max_side, max(1, int(round(w * max_side / h)))
    out = img.resize((nw, nh), Image.Resampling.LANCZOS)
    return encode_png(out), nw, nh, w / nw, h / nh


def pixel_hash(png: bytes, *, grid: int = 24) -> str:
    """Tiny perceptual hash: downsample and digest. Stable across mild JPEG-like noise."""
    img = decode_png(png).convert("L").resize((grid, grid), Image.Resampling.BILINEAR)
    return hashlib.sha1(img.tobytes()).hexdigest()[:16]


def hashes_close(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b and a == b)


def overlay_board_grid(png: bytes, white_at_bottom: bool = True) -> bytes:
    """Draw 8×8 lines and rank/file labels so the VLM can ground squares."""
    img = decode_png(png)
    w, h = img.size
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", max(10, min(w, h) // 22))
    except Exception:
        font = ImageFont.load_default()
    cw, ch = w / 8.0, h / 8.0
    for i in range(9):
        x = int(round(i * cw))
        y = int(round(i * ch))
        draw.line([(x, 0), (x, h)], fill=(20, 20, 20), width=2)
        draw.line([(0, y), (w, y)], fill=(20, 20, 20), width=2)
    files = "abcdefgh" if white_at_bottom else "hgfedcba"
    ranks = "87654321" if white_at_bottom else "12345678"
    for i, lab in enumerate(files):
        draw.text((int((i + 0.08) * cw), int(h - ch * 0.28)), lab, fill=(255, 220, 40), font=font)
    for i, lab in enumerate(ranks):
        draw.text((int(0.06 * cw), int((i + 0.08) * ch)), lab, fill=(255, 220, 40), font=font)
    return encode_png(img)


def cell_crop(board_png: bytes, square: str, white_at_bottom: bool) -> bytes:
    from .chess_geom import square_cell_px

    img = decode_png(board_png)
    w, h = img.size
    x0, y0, x1, y1 = square_cell_px(square, w, h, white_at_bottom, inset=0.12)
    return encode_png(img.crop((x0, y0, x1, y1)))
