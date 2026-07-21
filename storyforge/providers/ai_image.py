"""Free AI image generation per scene (Pollinations) — real depictions, no key/GPU.

Text-to-image from each scene's prompt, at the exact vertical size. The cutter then
animates the still (slow zoom/pan) into motion. This is the free, zero-setup way to
get ACTUAL AI-generated visuals of the subject instead of the abstract motion floor.
Priority is still: real AI video (Wan2GP/cloud) > stock footage > this > motion floor.
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path

from ..utils import env, http_download
from .base import GEN_NEGATIVE, ProviderError, VisualProvider


class AIImageProvider(VisualProvider):
    name = "ai_image"

    def __init__(self, cfg: dict | None = None, width: int = 1080, height: int = 1920):
        self.cfg = cfg or {}
        self.width = width
        self.height = height
        self.base = env("POLLINATIONS_URL", "https://image.pollinations.ai/prompt/")
        self.model = env("POLLINATIONS_MODEL", "flux")

    def available(self) -> bool:
        # opt-out only; it needs network but no key
        return not env("AI_IMAGE_DISABLE")

    def generate(self, scene: dict, workdir: str, seconds: float) -> str:
        idx = int(scene.get("_index", 0))
        style = self.cfg.get("style", "cinematic")
        prompt = f"{scene.get('image_prompt','')}, {style}{GEN_NEGATIVE}"
        seed = abs(hash(prompt)) % 100000
        url = (self.base + urllib.parse.quote(prompt[:900]) +
               f"?width={self.width}&height={self.height}&nologo=true&seed={seed}"
               f"&model={self.model}")
        dest = Path(workdir) / f"ai_{idx:02d}.jpg"
        try:
            http_download(url, dest, headers={"User-Agent": "StoryForge/1.0"}, timeout=90)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"pollinations failed: {exc}")
        if dest.stat().st_size < 2000:  # too small = error page, not an image
            raise ProviderError("pollinations returned no image")
        return str(dest)
