"""Procedural generative-MOTION provider (video, never a static photo).

When a real AI-video provider (Wan2GP / cloud) and stock footage are unavailable
— including full MOCK mode with no GPU and no network — this renders an actual
animated clip per scene with FFmpeg's generative sources (animated `gradients` +
hue drift + film grain + vignette + slow push). The output is real H.264 motion
video, which is what keeps Shortsmith "AI-generated videos, not photos" even
offline. It is the visual floor of the router: fast, deterministic, always video.
"""
from __future__ import annotations

import colorsys
from pathlib import Path

from ..utils import has_ffmpeg, run_ffmpeg
from .base import ProviderError, VisualProvider


def _hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return "0x%02X%02X%02X" % (int(r * 255), int(g * 255), int(b * 255))


class MotionProvider(VisualProvider):
    name = "motion"

    def __init__(self, cfg: dict | None = None, width: int = 1080, height: int = 1920):
        self.cfg = cfg or {}
        self.width = width
        self.height = height

    def available(self) -> bool:
        return has_ffmpeg()

    def generate(self, scene: dict, workdir: str, seconds: float) -> str:
        idx = int(scene.get("_index", 0))
        dur = max(2.0, round(float(seconds), 2))
        out = Path(workdir) / f"motion_{idx:02d}.mp4"
        w, h = self.width, self.height

        seed = abs(hash(scene.get("image_prompt", "") or str(idx))) % (2 ** 31)
        rng = (seed % 997) / 997.0
        hue = rng
        c0 = _hex(hue, 0.65, 0.10)               # deep base
        c1 = _hex(hue + 0.5, 0.55, 0.24)         # complementary shadow
        c2 = _hex(hue + 0.08, 0.85, 0.92)        # warm accent
        c3 = _hex(hue + 0.62, 0.7, 0.62)         # cool accent
        x0, y0 = int(w * (0.15 + 0.2 * rng)), int(h * (0.2 + 0.2 * rng))
        x1, y1 = int(w * (0.7 + 0.15 * rng)), int(h * (0.7 + 0.2 * rng))
        speed = round(0.008 + 0.014 * rng, 4)
        period = 8 + int(8 * rng)
        gtype = ["linear", "radial", "circular"][seed % 3]

        grad = (f"gradients=s={w}x{h}:c0={c0}:c1={c1}:c2={c2}:c3={c3}:nb_colors=4:"
                f"x0={x0}:y0={y0}:x1={x1}:y1={y1}:type={gtype}:speed={speed}:"
                f"seed={seed % 100000}:duration={dur}:rate=30")
        tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-pix_fmt", "yuv420p", "-t", f"{dur}", str(out.resolve())]

        # Attempt 1: full cinematic look
        vf1 = (f"hue=H=0.6*sin(2*PI*t/{period}),noise=alls=7:allf=t,"
               f"vignette=PI/5,format=yuv420p")
        ok, err = run_ffmpeg(["-f", "lavfi", "-i", grad, "-vf", vf1, *tail], cwd=workdir)
        if ok and out.exists():
            return str(out)

        # Attempt 2: gradients only (no post filters)
        ok, err = run_ffmpeg(["-f", "lavfi", "-i", grad, "-vf", "format=yuv420p", *tail], cwd=workdir)
        if ok and out.exists():
            return str(out)

        # Attempt 3: animated grain over a solid seeded color (still real motion)
        color = c0.replace("0x", "0x")
        ok, err = run_ffmpeg(
            ["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r=30:d={dur}",
             "-vf", "noise=alls=12:allf=t,vignette=PI/5,format=yuv420p", *tail], cwd=workdir)
        if ok and out.exists():
            return str(out)

        raise ProviderError(f"motion render failed: {err[-160:]}")
