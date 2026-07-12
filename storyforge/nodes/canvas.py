"""Stage 3 — canvas (the provider router).

For each scene: hero -> the lane's video provider (night=Wan2GP, day=cloud), the
rest -> stock motion; a PIL gradient card is the absolute last resort. Every
provider is retried 3x then the chain falls through, so a scene never has no
visual. Cloud cap exhaustion backfills remaining heroes with stock + fires 🟡.
"""
from __future__ import annotations

from pathlib import Path

from ..config import format_spec
from ..providers import (CapReached, CloudProvider, MotionProvider, StockProvider,
                         cap_remaining, gradient_card, lane_video_provider)
from ..providers.wan2gp import DeferredRender
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
    lane = state["lane"]
    mock = state.get("mock", False)
    scenes = state["script"]["scenes"]
    durations = state.get("durations", [])
    workdir = str(Path(state["workdir"]) / "visuals")
    Path(workdir).mkdir(parents=True, exist_ok=True)

    spec = format_spec(cfg)
    # The visual FLOOR is generative MOTION video (never a static photo).
    motion = MotionProvider(cfg, width=spec["width"], height=spec["height"])
    stock = StockProvider(cfg)
    hero_provider = lane_video_provider(lane, cfg) if not mock else None
    hero_avail = bool(hero_provider and hero_provider.available())

    n_hero = sum(1 for s in scenes if s.get("hero"))
    cap_note = ""
    if not mock and lane == "day" and isinstance(hero_provider, CloudProvider) and hero_avail:
        cap_note = f", cloud cap remaining={cap_remaining()}"
    progress(state, "canvas",
             f"routing {len(scenes)} scenes ({n_hero} hero) — "
             f"hero provider {'ready' if hero_avail else 'stock-backfill'}{cap_note}",
             extra={"hero_provider": getattr(hero_provider, 'name', 'none'),
                    "hero_available": hero_avail,
                    "cloud_cap_remaining": cap_remaining() if lane == 'day' else None})

    visual_paths: list[str] = []
    visual_kinds: list[str] = []
    hero_results: dict[str, str] = {}
    cloud_capped = False

    for i, sc in enumerate(scenes):
        sc = {**sc, "_index": i}
        seconds = durations[i] if i < len(durations) else 5.0
        is_hero = bool(sc.get("hero"))

        # Build the provider chain for this scene. The floor is always MOTION.
        chain = []
        if mock:
            chain = [motion]
        elif is_hero and hero_avail and not (cloud_capped and isinstance(hero_provider, CloudProvider)):
            chain = [hero_provider, stock, motion]
        elif stock.available():
            chain = [stock, motion]
        else:
            chain = [motion]

        path = None
        used = None
        for provider in chain:
            try:
                path = _try_provider(provider, sc, workdir, seconds)
                used = provider.name
                break
            except DeferredRender:
                # async night-lane pre-queue: abort so a later cron collects the clips
                raise
            except CapReached as exc:
                cloud_capped = True
                add_fallback(state, "canvas",
                             f"scene {i+1}: {exc} -> backfilling remaining heroes with stock")
                continue  # fall through to stock/motion in this same chain
            except Exception as exc:  # noqa: BLE001
                if provider is not chain[-1]:
                    add_fallback(state, "canvas",
                                 f"scene {i+1}: {provider.name} failed ({exc}) -> next provider")
                continue

        if path is None:  # motion itself failed (ffmpeg broken) -> break-glass still image
            path = gradient_card({**sc}, workdir, index=i, size=(spec["width"], spec["height"]))
            used = "gradient(break-glass)"
            add_fallback(state, "canvas", f"scene {i+1}: motion failed, static break-glass card")

        if is_hero:
            hero_results[str(i)] = used
        visual_paths.append(path)
        visual_kinds.append(_kind(path))

    real_clips = sum(1 for k in visual_kinds if k == "video")
    progress(state, "canvas",
             f"visuals ready: {real_clips} motion clips, "
             f"{len(visual_kinds)-real_clips} cards; hero via {sorted(set(hero_results.values())) or ['—']}")
    return {"visual_paths": visual_paths, "visual_kinds": visual_kinds,
            "hero_results": hero_results}
