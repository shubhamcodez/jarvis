"""Generate Tauri PNG/ICO icons from frontend/public/Ada.jpg."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "public" / "Ada.jpg"
OUT = ROOT / "src-tauri" / "icons"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.open(SRC).convert("RGBA")
    for size, name in ((32, "32x32.png"), (128, "128x128.png"), (256, "icon.png")):
        img.resize((size, size), Image.Resampling.LANCZOS).save(OUT / name)
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(OUT / "icon.ico", sizes=ico_sizes)
    print(f"Wrote icons in {OUT}")


if __name__ == "__main__":
    main()
