#!/usr/bin/env python
"""Generate StoryForge PWA icons (192, 512, 512-maskable) into app/pwa/icons/.

Pure Pillow; run once. Re-run any time to regenerate.
    python scripts/gen_icons.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ICONS = Path(__file__).resolve().parents[1] / "app" / "pwa" / "icons"
ICONS.mkdir(parents=True, exist_ok=True)


def _font(size):
    for p in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            if Path(p).exists():
                return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def make(size: int, maskable: bool = False) -> Image.Image:
    img = Image.new("RGB", (size, size), (13, 17, 23))
    px = img.load()
    # diagonal gradient: deep indigo -> violet
    c1, c2 = (36, 24, 92), (124, 58, 173)
    for y in range(size):
        for x in range(0, size, 2):
            t = (x + y) / (2 * size)
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            px[x, y] = (r, g, b)
            if x + 1 < size:
                px[x + 1, y] = (r, g, b)
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    # soft glow ring
    for i, rad in enumerate(range(int(size * 0.42), int(size * 0.30), -6)):
        a = 255 - i * 22
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=(255, 214, 10, max(0, a)), width=2)
    # play triangle
    s = size * (0.16 if not maskable else 0.13)
    d.polygon([(cx - s * 0.6, cy - s), (cx - s * 0.6, cy + s), (cx + s, cy)], fill=(255, 214, 10))
    # SF wordmark
    f = _font(int(size * 0.15))
    text = "SF"
    tb = d.textbbox((0, 0), text, font=f)
    d.text((cx - (tb[2] - tb[0]) / 2, cy + size * 0.24 - (tb[3] - tb[1]) / 2), text,
           font=f, fill=(230, 237, 243))
    return img


def main():
    make(192).save(ICONS / "icon-192.png")
    make(512).save(ICONS / "icon-512.png")
    make(512, maskable=True).save(ICONS / "icon-512-maskable.png")
    print("icons written to", ICONS)


if __name__ == "__main__":
    main()
