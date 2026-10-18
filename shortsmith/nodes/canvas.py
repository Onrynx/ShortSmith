"""Stage 3 — canvas: give every scene something to show.

The chain, in order, with each step falling through to the next:

    background  your gameplay clips in assets/backgrounds (the normal path)
    motion      a generated moving background, when there are no clips
    motion      generated moving colour — never fails, needs nothing
    gradient    a still card, only if even ffmpeg is broken

V1 also carried three optional providers here: Wan2GP hero clips, a paid cloud
video API, and Pexels/Pixabay stock. All three needed keys or a separate server,
all three reported unavailable on every real run, and they pulled a cloud quota
counter and a "hero scene" routing concept through the whole stage. They are
gone, which is why this file is now about half its old length.
"""
from __future__ import annotations

from pathlib import Path

from ..config import format_spec
from ..providers import (BackgroundProvider, MotionProvider,
                         gradient_card)
from ..utils import retry
from .common import add_fallback, progress

VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")


def _kind(path: str) -> str:
    return "video" if Path(path).suffix.lower() in VIDEO_EXTS else "image"


def _try_provider(provider, scene, workdir, seconds):
    return retry(lambda: provider.generate(scene, workdir, seconds),
                 tries=3, delay=2.0, label=f"{provider.name}")


def node_canvas(state: dict) -> dict:
    cfg = state["channel_cfg"]
    mock = state.get("mock", False)
    scenes = state["script"]["scenes"]
    durations = state.get("durations", [])
    workdir = str(Path(state["workdir"]) / "visuals")
    Path(workdir).mkdir(parents=True, exist_ok=True)

    spec = format_spec(cfg)
    motion = MotionProvider(cfg, width=spec["width"], height=spec["height"])
    background = BackgroundProvider(cfg)
    bg_avail = background.available()
    if not bg_avail:
        # This used to fall through to an online image generator, which meant a
        # fresh install with an empty backgrounds folder quietly posted every
        # scene description to a third-party service — while the app said
        # nothing leaves this computer. The honest fallback is the local motion
        # floor plus a message telling you what to add.
        add_fallback(state, "canvas",
                     "no clips in assets/backgrounds/ — using the plain moving "
                     "background. Add clips under Backgrounds for the gameplay look.")

    chain = ([background] if bg_avail else []) + [motion]

    progress(state, "canvas",
             f"building visuals for {len(scenes)} scenes via "
             f"{' -> '.join(p.name for p in chain)}")

    visual_paths: list[str] = []
    visual_kinds: list[str] = []

    for i, sc in enumerate(scenes):
        sc = {**sc, "_index": i}
        seconds = durations[i] if i < len(durations) else 5.0
        path = None
        for provider in chain:
            try:
                path = _try_provider(provider, sc, workdir, seconds)
                break
            except Exception as exc:  # noqa: BLE001
                if provider is not chain[-1]:
                    add_fallback(state, "canvas",
                                 f"scene {i+1}: {provider.name} failed ({exc}) -> next")
                continue

        if path is None:    # motion itself failed, so ffmpeg is broken
            path = gradient_card({**sc}, workdir, index=i,
                                 size=(spec["width"], spec["height"]))
            add_fallback(state, "canvas",
                         f"scene {i+1}: motion failed, using a still card")

        visual_paths.append(path)
        visual_kinds.append(_kind(path))

    clips = sum(1 for k in visual_kinds if k == "video")
    progress(state, "canvas",
             f"visuals ready: {clips} moving, {len(visual_kinds) - clips} still")
    return {"visual_paths": visual_paths, "visual_kinds": visual_kinds}
