"""Stage 1 — script.

Runs the CrewAI creative crew; falls back single-shot LLM -> canned mock script.
Validates the schema, enforces scene1 == hook and the AI disclosure line, and
clamps hero count to the lane budget. Never hard-fails: the canned script is the
floor.
"""
from __future__ import annotations

from ..config import format_spec, lane_budget
from ..crews.story_crew import mock_script, run_creative_crew, single_shot_script
from ..llm import select_model
from ..schema import (clamp_heroes, ensure_disclosure, normalize_script,
                      validate_script)
from ..utils import log
from .common import add_fallback, progress


def _finalize(raw: dict, hero_budget: tuple[int, int],
              scene_bounds: tuple[int, int]) -> dict:
    """Normalize + validate an LLM script. Raises ValueError if unsalvageable."""
    script = normalize_script(raw, hero_budget, scene_bounds)
    # hard guarantees the validator also checks (belt and suspenders)
    if script.get("scenes"):
        script["scenes"][0]["narration"] = script.get("hook") or script["scenes"][0]["narration"]
        script["scenes"] = clamp_heroes(script["scenes"], hero_budget)
    script["description"] = ensure_disclosure(script.get("description", ""))
    errs = validate_script(script, hero_budget, scene_bounds)
    if errs:
        raise ValueError("; ".join(errs))
    return script


def node_script(state: dict) -> dict:
    cfg = state["channel_cfg"]
    lane = state["lane"]
    topic = state.get("topic", "")
    mock = state.get("mock", False)
    hero_budget = lane_budget(cfg, lane)

    # The chosen narrator changes how fast the words are spoken, so it changes how
    # MANY words a 2.5-minute video needs. Squeaky runs at 1.30x (~195 wpm) vs
    # Adam's 1.18x (~177 wpm), so it needs a proportionally LONGER script to fill
    # the same runtime. Bake the real speed into cfg before budgeting words.
    from .voice import VOICE_STYLES, resolve_style
    style_name, style = resolve_style(state, cfg)
    ratio = style["speed"] / VOICE_STYLES["adam"]["speed"]
    cfg = {**cfg, "voice_speed": float(cfg.get("voice_speed", 1.18) or 1.18) * ratio}
    state["voice_style"] = style_name

    scene_bounds = format_spec(cfg)["scene_bounds"]

    model_info = state.get("model_info") or select_model(mock)
    log("info", f"LLM: {model_info['reason']}")
    progress(state, "script", f"generating script via {model_info['backend']}")

    script = None
    source = None

    if not mock and model_info["backend"] != "mock":
        # 1) CrewAI crew
        try:
            raw = run_creative_crew(cfg, lane, topic, hero_budget, model_info)
            script, source = _finalize(raw, hero_budget, scene_bounds),"crew"
        except Exception as exc:  # noqa: BLE001
            add_fallback(state, "script", f"crew unavailable ({exc}); trying single-shot LLM")
            # 2) single-shot LLM
            try:
                raw = single_shot_script(cfg, lane, topic, hero_budget, model_info)
                script, source = _finalize(raw, hero_budget, scene_bounds),"single_shot"
            except Exception as exc2:  # noqa: BLE001
                add_fallback(state, "script", f"single-shot failed ({exc2}); using canned script")

    # 3) canned mock (the floor — must NEVER fail validation or the lane dies).
    # Relax scene bounds to whatever the canned script actually has: a valid
    # 11-scene video beats no video at all.
    if script is None:
        raw = mock_script(cfg, lane, topic, hero_budget)
        n = len(raw.get("scenes") or []) or scene_bounds[0]
        relaxed = (min(scene_bounds[0], n), max(scene_bounds[1], n))
        script, source = _finalize(raw, hero_budget, relaxed), "mock"

    # Remember LLM-written stories so future runs can't repeat them.
    if source in ("crew", "single_shot"):
        try:
            from ..story_memory import remember
            remember(script.get("title", ""), script.get("hook", ""))
        except Exception as exc:  # noqa: BLE001
            log("warn", f"could not record story memory: {exc}")

    heroes = sum(1 for s in script["scenes"] if s.get("hero"))
    progress(state, "script",
             f"script ready via {source}: '{script['title']}' "
             f"({len(script['scenes'])} scenes, {heroes} hero)")
    return {"script": script, "script_source": source,
            "hero_budget": list(hero_budget), "model_info": model_info}
