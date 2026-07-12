"""LANE B (day / cloud) hero-clip provider: Veo (Google AI Studio) or Seedance.

One short hero clip per call. A per-day cap is enforced in code; when the cap is
hit the provider raises CapReached, which the canvas node turns into a stock
backfill for the remaining hero scenes plus a 🟡 notification.

The video-gen APIs are long-running + evolving, so the Veo path uses the AI Studio
long-running-operation shape and degrades gracefully; the daily-cap accounting is
exact and testable regardless of the backend.

Env:
  CLOUD_VIDEO_PROVIDER   veo | seedance | auto  (default auto)
  CLOUD_DAILY_CAP        int, default 5
  GEMINI_API_KEY         used for Veo
  VEO_MODEL              default veo-3.0-generate-preview
  SEEDANCE_URL / SEEDANCE_API_KEY   Seedance endpoint + key
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

from ..utils import (STATE_DIR, env, http_download, http_get_json, http_post_json,
                     log, read_json, today_str, write_json)
from .base import GEN_NEGATIVE, ProviderError, VisualProvider

CAP_FILE = STATE_DIR / "cloud_cap.json"


class CapReached(ProviderError):
    """The cloud daily cap is exhausted for today."""


def _cap_state() -> dict:
    return read_json(CAP_FILE, {}) or {}


def cap_remaining() -> int:
    cap = int(env("CLOUD_DAILY_CAP", "5") or 5)
    used = _cap_state().get(today_str(), 0)
    return max(0, cap - int(used))


def _consume_cap() -> None:
    state = _cap_state()
    day = today_str()
    state[day] = int(state.get(day, 0)) + 1
    # keep only recent days tidy
    state = {k: v for k, v in state.items() if k >= day[:8] + "01" or k == day}
    write_json(CAP_FILE, state)


class CloudProvider(VisualProvider):
    name = "cloud"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.provider = (env("CLOUD_VIDEO_PROVIDER", "auto") or "auto").lower()
        self.gemini_key = env("GEMINI_API_KEY")
        self.veo_model = env("VEO_MODEL", "veo-3.0-generate-preview")
        self.seedance_url = env("SEEDANCE_URL")
        self.seedance_key = env("SEEDANCE_API_KEY")

    def _which(self) -> str | None:
        if self.provider == "veo" and self.gemini_key:
            return "veo"
        if self.provider == "seedance" and self.seedance_key:
            return "seedance"
        if self.provider == "auto":
            if self.gemini_key:
                return "veo"
            if self.seedance_key:
                return "seedance"
        return None

    def available(self) -> bool:
        return self._which() is not None

    # -- Veo via Google AI Studio (long-running operation) ---------------- #
    def _veo(self, prompt: str, dest: Path) -> str:
        base = "https://generativelanguage.googleapis.com/v1beta"
        start = f"{base}/models/{self.veo_model}:predictLongRunning?key={self.gemini_key}"
        body = {"instances": [{"prompt": prompt}],
                "parameters": {"aspectRatio": "16:9", "personGeneration": "dont_allow"}}
        op = http_post_json(start, body, timeout=60)
        op_name = op.get("name") if isinstance(op, dict) else None
        if not op_name:
            raise ProviderError(f"Veo did not return an operation: {str(op)[:200]}")
        deadline = time.time() + float(env("VEO_POLL_TIMEOUT", "600") or 600)
        while time.time() < deadline:
            status = http_get_json(f"{base}/{op_name}?key={self.gemini_key}", timeout=30)
            if status.get("done"):
                try:
                    resp = status["response"]
                    vids = (resp.get("generatedVideos") or
                            resp.get("generateVideoResponse", {}).get("generatedSamples") or [])
                    sample = vids[0]
                    uri = (sample.get("video", {}) or {}).get("uri") or sample.get("uri")
                    if uri:
                        sep = "&" if "?" in uri else "?"
                        http_download(f"{uri}{sep}key={self.gemini_key}", dest, timeout=180)
                        return str(dest)
                    b64 = (sample.get("video", {}) or {}).get("bytesBase64Encoded")
                    if b64:
                        dest.write_bytes(base64.b64decode(b64))
                        return str(dest)
                except Exception as exc:  # noqa: BLE001
                    raise ProviderError(f"unexpected Veo response: {exc}")
                raise ProviderError("Veo done but no video payload found")
            time.sleep(10)
        raise ProviderError("Veo operation timed out")

    # -- Seedance (generic REST) ------------------------------------------ #
    def _seedance(self, prompt: str, dest: Path) -> str:
        headers = {"Authorization": f"Bearer {self.seedance_key}"}
        payload = {"prompt": prompt, "resolution": "720p", "duration": 5, "ratio": "16:9"}
        data = http_post_json(self.seedance_url, payload, headers=headers, timeout=120)
        url = None
        if isinstance(data, dict):
            url = data.get("video_url") or data.get("url") or \
                (data.get("data", {}) or {}).get("video_url")
        if not url:
            raise ProviderError(f"Seedance returned no video url: {str(data)[:200]}")
        http_download(url, dest, timeout=180)
        return str(dest)

    def generate(self, scene: dict, workdir: str, seconds: float) -> str:
        which = self._which()
        if not which:
            raise ProviderError("no cloud video key (GEMINI_API_KEY / SEEDANCE_API_KEY)")
        if cap_remaining() <= 0:
            raise CapReached(f"cloud daily cap reached ({env('CLOUD_DAILY_CAP', '5')})")

        idx = int(scene.get("_index", 0))
        dest = Path(workdir) / f"cloud_{idx:02d}.mp4"
        prompt = (scene.get("image_prompt", "") + GEN_NEGATIVE).strip()

        path = self._veo(prompt, dest) if which == "veo" else self._seedance(prompt, dest)
        _consume_cap()
        log("info", f"cloud[{which}] hero scene {idx+1}; cap remaining {cap_remaining()}")
        return path
