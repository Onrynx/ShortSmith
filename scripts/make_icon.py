"""Generate StoryForge's app icon.

Uses Pillow, which the pipeline already depends on, so the icon can be rebuilt
on any machine without design tools. Produces:

    app/icon.png    1024x1024 master
    app/icon.ico    Windows (multi-resolution, 16 -> 256)
    app/icon.icns   macOS, when Pillow's ICNS writer is available

The mark is a forge spark over a vertical frame: the 9:16 shape StoryForge
outputs, with an ember rising out of it. Flat shapes only — an icon is usually
seen at 32 px, where gradients and detail turn to mud.

Run:  python scripts/make_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app"

BG_TOP = (28, 20, 32)
BG_BOT = (16, 14, 20)
EMBER = (255, 122, 61)
EMBER_HOT = (255, 190, 70)
FRAME = (248, 243, 238)


def build(size: int = 1024):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 1024.0          # everything below is authored at 1024

    # One solid plate. An earlier two-tone version left a hard seam across the
    # middle that looked like a rendering fault at small sizes.
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(220 * s), fill=BG_TOP)

    # The 9:16 frame — the shape StoryForge outputs.
    fw, fh = int(330 * s), int(580 * s)
    fx, fy = (size - fw) // 2, int(250 * s)
    d.rounded_rectangle([fx, fy, fx + fw, fy + fh], radius=int(46 * s),
                        outline=FRAME, width=int(32 * s))

    # The flame fills the frame and breaks through its top edge, so the mark
    # reads as something coming OUT of the video rather than sitting in it.
    cx = size // 2
    tip_y = int(168 * s)
    base_y = fy + fh - int(74 * s)
    # Keep the flame inside the frame's inner width (half-width minus the
    # stroke), or it crosses the side strokes and reads as a drawing error.
    half = (fw // 2) - int(32 * s) - int(16 * s)
    d.polygon([(cx, tip_y), (cx + half, base_y), (cx - half, base_y)], fill=EMBER)
    inner = int(half * 0.52)
    d.polygon([(cx, int(340 * s)), (cx + inner, base_y),
               (cx - inner, base_y)], fill=EMBER_HOT)

    # Sparks, placed outside the frame so they survive the 32px shrink.
    for dx, dy, rad in ((-250, 300, 34), (246, 360, 26), (-214, 452, 19)):
        rr = int(rad * s)
        d.ellipse([cx + int(dx * s) - rr, int(dy * s) - rr,
                   cx + int(dx * s) + rr, int(dy * s) + rr], fill=EMBER)
    return img


def main() -> int:
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Pillow is required:  pip install Pillow")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    master = build(1024)
    png = OUT / "icon.png"
    master.save(png)
    print(f"wrote {png.relative_to(ROOT)}")

    ico = OUT / "icon.ico"
    master.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                            (64, 64), (128, 128), (256, 256)])
    print(f"wrote {ico.relative_to(ROOT)}")

    icns = OUT / "icon.icns"
    try:
        master.resize((1024, 1024)).save(icns)
        print(f"wrote {icns.relative_to(ROOT)}")
    except Exception as exc:  # noqa: BLE001
        # Pillow cannot always write ICNS. The .app launcher falls back to the
        # PNG, and `iconutil` on a Mac can convert properly if it matters.
        print(f"(skipped icon.icns: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
