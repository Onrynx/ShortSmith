"""Stock motion footage: Pexels Video API + Pixabay Video API (free keys).

Unlimited free tier, so this is the backfill for every non-hero scene and the
first fallback when a hero provider fails. Query is derived from scene keywords.
Downloads a landscape clip; the cutter trims/loops it to the audio duration.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..utils import env, http_download, http_get_json, log
from .base import ProviderError, VisualProvider

_STOP = {
    "a", "an", "the", "of", "in", "on", "at", "with", "and", "to", "into", "over",
    "under", "close", "up", "shot", "cinematic", "moody", "dramatic", "epic",
    "painterly", "concept", "art", "volumetric", "light", "film", "grain", "style",
    "detailed", "atmospheric", "no", "text", "logo", "watermark", "real", "people",
}


def keywords_from_scene(scene: dict, cfg: dict | None = None, max_words: int = 5) -> str:
    """Build a stock search query from a scene's visual subject."""
    prompt = scene.get("image_prompt", "") or scene.get("narration", "")
    subject = prompt.split(",")[0]  # the part before style descriptors is usually the subject
    words = re.findall(r"[A-Za-z]+", subject.lower())
    kept = [w for w in words if w not in _STOP and len(w) > 2]
    query = " ".join(kept[:max_words]).strip()
    if not query and cfg:
        query = str(cfg.get("niche", "cinematic landscape"))
    return query or "cinematic landscape"


class StockProvider(VisualProvider):
    name = "stock"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.pexels_key = env("PEXELS_API_KEY")
        self.pixabay_key = env("PIXABAY_API_KEY")

    def available(self) -> bool:
        return bool(self.pexels_key or self.pixabay_key)

    # -- source: Pexels -------------------------------------------------- #
    def _pexels(self, query: str, dest: Path) -> str | None:
        url = (f"https://api.pexels.com/videos/search?query={query}"
               f"&orientation=landscape&size=medium&per_page=8")
        data = http_get_json(url, headers={"Authorization": self.pexels_key}, timeout=20)
        best = None
        for vid in data.get("videos", []):
            for vf in vid.get("video_files", []):
                if vf.get("width") and vf.get("link") and (vf.get("height") or 0) <= (vf.get("width") or 1):
                    # prefer ~1080p landscape h264 mp4
                    score = -abs((vf.get("height") or 0) - 1080)
                    if best is None or score > best[0]:
                        best = (score, vf["link"])
        if best:
            http_download(best[1], dest, timeout=120)
            return str(dest)
        return None

    # -- source: Pixabay ------------------------------------------------- #
    def _pixabay(self, query: str, dest: Path) -> str | None:
        url = (f"https://pixabay.com/api/videos/?key={self.pixabay_key}"
               f"&q={query.replace(' ', '+')}&per_page=8")
        data = http_get_json(url, timeout=20)
        for hit in data.get("hits", []):
            streams = hit.get("videos", {})
            for q in ("large", "medium", "small"):
                s = streams.get(q) or {}
                if s.get("url"):
                    http_download(s["url"], dest, timeout=120)
                    return str(dest)
        return None

    def generate(self, scene: dict, workdir: str, seconds: float) -> str:
        if not self.available():
            raise ProviderError("no PEXELS_API_KEY or PIXABAY_API_KEY set")
        query = keywords_from_scene(scene, self.cfg)
        idx = int(scene.get("_index", 0))
        dest = Path(workdir) / f"stock_{idx:02d}.mp4"

        errors = []
        for source_name, fn, key in (("pexels", self._pexels, self.pexels_key),
                                     ("pixabay", self._pixabay, self.pixabay_key)):
            if not key:
                continue
            try:
                path = fn(query, dest)
                if path:
                    log("info", f"stock[{source_name}] '{query}' -> scene {idx+1}")
                    return path
                errors.append(f"{source_name}: no landscape result for '{query}'")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source_name}: {exc}")
        raise ProviderError("stock exhausted: " + "; ".join(errors))
