"""Visual provider interface + the gradient-card last resort.

Every provider implements the same tiny contract:

    VisualProvider.generate(scene, workdir, seconds) -> path

`path` points at a real file — a video (.mp4/.mov/.webm) or an image (.png/.jpg).
The canvas node infers which by extension. `available()` lets the router skip a
provider that has no keys/endpoint without treating it as a failure.

The gradient card is the ABSOLUTE last resort so a run never hard-fails on visuals.
It is also what MOCK mode uses for every scene (zero network). It is deliberately
abstract and text-free (cinematic color field + glow + grain + vignette); the
cutter animates it with a slow zoom/pan so it is never a static slideshow.
"""
from __future__ import annotations

import colorsys
from pathlib import Path

# The negative suffix appended to EVERY generative prompt (wan2gp / cloud).
GEN_NEGATIVE = ", no text, no watermark, no logo, no real people"


class ProviderError(RuntimeError):
    """Recoverable provider failure — caller should fall back."""


class VisualProvider:
    name = "base"

    def available(self) -> bool:
        return True

    def generate(self, scene: dict, workdir: str, seconds: float) -> str:
        raise NotImplementedError


def _hsv(h: float, s: float, v: float):
    import numpy as np
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return np.array([r, g, b], dtype="float32")


def gradient_card(scene: dict, workdir: str, index: int = 0,
                  size: tuple[int, int] = (1920, 1080), seed: int | None = None) -> str:
    """Render a cinematic abstract still. Deterministic per scene prompt.

    Uses numpy + Pillow (both always present). Never contains text/logos.
    """
    import numpy as np
    from PIL import Image

    w, h = size
    prompt = scene.get("image_prompt", "") or scene.get("narration", "") or str(index)
    if seed is None:
        seed = abs(hash(prompt)) % (2 ** 32)
    rng = np.random.default_rng(seed)

    hue = rng.random()
    shadow = _hsv(hue, 0.55, 0.16)                 # deep base
    accent = _hsv(hue + rng.uniform(0.04, 0.12), 0.62, 0.72)  # light accent

    yy, xx = np.mgrid[0:h, 0:w].astype("float32")
    angle = rng.uniform(0, np.pi)
    proj = xx * np.cos(angle) + yy * np.sin(angle)
    proj = (proj - proj.min()) / (np.ptp(proj) + 1e-6)
    img = shadow[None, None, :] * (1 - proj[..., None]) + accent[None, None, :] * proj[..., None]

    # soft off-center glow for depth
    cx, cy = w * rng.uniform(0.3, 0.7), h * rng.uniform(0.28, 0.6)
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    diag = np.hypot(w, h)
    glow = np.clip(1 - r / (0.55 * diag), 0, 1)[..., None]
    img = img + glow * _hsv(hue + 0.05, 0.35, 0.9)[None, None, :] * 0.35

    # a second faint blob for painterly variety
    bx, by = w * rng.uniform(0.2, 0.8), h * rng.uniform(0.4, 0.9)
    rb = np.sqrt((xx - bx) ** 2 + (yy - by) ** 2)
    blob = np.clip(1 - rb / (0.35 * diag), 0, 1)[..., None]
    img = img + blob * accent[None, None, :] * 0.12

    # film grain + vignette (monetization-friendly texture, never flat)
    img = img + rng.normal(0, 0.018, (h, w, 1)).astype("float32")
    vig = np.clip(1 - (r / (0.62 * diag)) ** 2 * 0.7, 0.15, 1)[..., None]
    img = np.clip(img * vig, 0, 1)

    arr = (img * 255).astype("uint8")
    out = Path(workdir) / f"card_{index:02d}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGB").save(out)
    return str(out)


class GradientProvider(VisualProvider):
    """Last-resort / mock provider. Always available, never touches the network."""
    name = "gradient"

    def generate(self, scene: dict, workdir: str, seconds: float) -> str:
        return gradient_card(scene, workdir, index=int(scene.get("_index", 0)))
