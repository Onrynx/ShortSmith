"""Gameplay-background provider — the classic Shorts format (Minecraft parkour etc.).

Plays CONTINUOUS background footage behind the whole video: each scene gets the
next consecutive window of the same clip, so concatenated segments look like one
unbroken run. Drop any gameplay mp4/mkv/webm files into assets/backgrounds/
("no copyright Minecraft parkour" clips, or record your own gameplay — add your
own touch per Mojang's usage guidelines). A random clip + random start is chosen
per run; scenes then advance through it sequentially.
"""
from __future__ import annotations

import random
from pathlib import Path

from ..utils import ASSETS_DIR, ffprobe_duration, read_json, run_ffmpeg, write_json
from .base import ProviderError, VisualProvider

BG_DIR = ASSETS_DIR / "backgrounds"
BG_DIR.mkdir(parents=True, exist_ok=True)
_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")


def background_clips() -> list[Path]:
    return [p for p in BG_DIR.iterdir() if p.suffix.lower() in _EXTS] if BG_DIR.exists() else []


class BackgroundProvider(VisualProvider):
    name = "background"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}

    def available(self) -> bool:
        return len(background_clips()) > 0

    def _state(self, workdir: str) -> dict:
        return read_json(Path(workdir) / "bg_state.json", {}) or {}

    def generate(self, scene: dict, workdir: str, seconds: float) -> str:
        clips = background_clips()
        if not clips:
            raise ProviderError("no clips in assets/backgrounds/")
        idx = int(scene.get("_index", 0))
        st = self._state(workdir)

        if "clip" not in st or not Path(st["clip"]).exists():
            clip = random.choice(clips)
            dur = ffprobe_duration(clip)
            # random start, keeping ~70s of runway; wraps via loop below if short
            start = random.uniform(0, max(0.0, dur - 75.0))
            st = {"clip": str(clip), "dur": dur, "cursor": start}
        clip = Path(st["clip"])
        dur = float(st.get("dur") or ffprobe_duration(clip) or 1.0)
        cursor = float(st.get("cursor", 0.0))
        if cursor + seconds > dur - 0.5:   # ran out of clip — wrap to the top
            cursor = 0.5

        out = Path(workdir) / f"bg_{idx:02d}.mp4"
        ok, err = run_ffmpeg(["-ss", f"{cursor:.2f}", "-i", str(clip.resolve()),
                              "-t", f"{seconds + 0.3:.2f}", "-an",
                              "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                              str(out.resolve())], cwd=workdir, timeout=300)
        if not ok or not out.exists():
            raise ProviderError(f"background cut failed: {err[-120:]}")

        st["cursor"] = cursor + seconds
        write_json(Path(workdir) / "bg_state.json", st)
        return str(out)
