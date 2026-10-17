"""The pipeline state that flows through the LangGraph assembly line.

A plain TypedDict so LangGraph is NOT required to import this module (the lite
sequential runner uses the same dict). Nodes return partial dict updates.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from .utils import OUTPUT_DIR, ensure_dir, now_id, slugify


class PipelineState(TypedDict, total=False):
    # --- inputs / identity ---
    channel: str                 # channel key, e.g. "gossip_stories"
    channel_cfg: dict            # loaded config
    lane: str                    # "night" | "day"
    mock: bool
    publish: bool
    topic: str                   # optional forced topic ("" if none)
    run_id: str
    workdir: str                 # per-run working directory

    # --- llm ---
    model_info: dict

    # --- script stage ---
    script: dict                 # validated script JSON
    hero_budget: list            # [lo, hi] for this lane
    script_source: str           # "crew" | "single_shot" | "mock"

    # --- voice stage ---
    audio_paths: list            # wav per scene
    durations: list              # seconds per scene

    # --- canvas stage ---
    visual_paths: list           # video/image per scene
    visual_kinds: list           # "video" | "image" per scene
    hero_results: dict           # provider used per hero scene

    # --- cutter stage ---
    video_path: str              # assembled mp4 (in workdir)
    captions_kind: str           # "word" | "scene" | "none"

    # --- herald stage ---
    title: str
    description: str
    tags: list
    thumbnail_path: str
    final_video_path: str        # mp4 in pending/
    report_path: str
    published: bool

    # --- cross-cutting bookkeeping ---
    stage: str                   # current stage name
    events: list                 # bottleneck feed entries
    fallbacks: list              # human-readable fallback notes
    errors: list
    started_at: str


def new_run_state(channel: str, channel_cfg: dict, lane: str, *,
                  mock: bool = False, publish: bool = False, topic: str = "",
                  run_id: str | None = None) -> PipelineState:
    run_id = run_id or f"{now_id()}_{lane}"
    workdir = ensure_dir(OUTPUT_DIR / channel / "_work" / run_id)
    st: PipelineState = {
        "channel": channel,
        "channel_cfg": channel_cfg,
        "lane": lane,
        "mock": mock,
        "publish": publish,
        "topic": topic or "",
        "run_id": run_id,
        "workdir": str(workdir),
        "audio_paths": [],
        "durations": [],
        "visual_paths": [],
        "visual_kinds": [],
        "hero_results": {},
        "events": [],
        "fallbacks": [],
        "errors": [],
        "stage": "init",
    }
    return st


def pending_dir(channel: str) -> Path:
    return ensure_dir(OUTPUT_DIR / channel / "pending")


def approved_dir(channel: str) -> Path:
    return ensure_dir(OUTPUT_DIR / channel / "approved")


def run_basename(state: PipelineState) -> str:
    """Stable public basename for outputs: <channel>_<lane>_<slug>_<runid>."""
    title = (state.get("script") or {}).get("title", "") or state.get("topic", "") or "story"
    return f"{state['channel']}_{state['lane']}_{slugify(title, 40)}_{state['run_id']}"
