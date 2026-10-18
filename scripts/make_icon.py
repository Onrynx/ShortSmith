"""Generate Shortsmith's app icon.

Uses Pillow, which the pipeline already depends on, so the icon can be rebuilt
on any machine without design tools. Produces:

    app/icon.png    1024x1024 master
    app/icon.ico    Windows (multi-resolution, 16 -> 256)
    app/icon.icns   macOS, when Pillow's ICNS writer is available

The mark is an anvil with sparks coming off it. An earlier attempt drew a
hammer across a 9:16 frame and it read as a phone with a line through it — at
icon size a single unmistakable silhouette beats a clever composition. The
anvil profile (horn, waist, splayed base) survives being shrunk to 32 px.

Run:  python scripts/make_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app"

BG_TOP = (28, 20, 32)
EMBER = (255, 122, 61)
EMBER_HOT = (255, 190, 70)
FRAME = (248, 243, 238)


def build(size: int = 1024):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 1024.0          # authored at 1024

    def S(v):
        return int(v * s)

    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=S(220), fill=BG_TOP)

    # ---- anvil ----------------------------------------------------------- #
    # face: the long flat top the smith works on
    d.rounded_rectangle([S(250), S(430), S(770), S(530)], radius=S(16), fill=EMBER)
    # horn: tapers off the left of the face
    d.polygon([(S(250), S(440)), (S(250), S(524)), (S(108), S(500))], fill=EMBER)
    # waist: the narrow column under the face
    d.polygon([(S(404), S(530)), (S(616), S(530)),
               (S(576), S(654)), (S(444), S(654))], fill=EMBER)
    # base: splayed foot, slightly brighter so the form reads at small sizes
    d.rounded_rectangle([S(316), S(654), S(704), S(762)], radius=S(18),
                        fill=EMBER_HOT)

    # ---- sparks ---------------------------------------------------------- #
    for x, y, r in ((352, 318, 34), (520, 250, 24), (646, 330, 18),
                    (250, 250, 14)):
        d.ellipse([S(x - r), S(y - r), S(x + r), S(y + r)], fill=EMBER_HOT)
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
