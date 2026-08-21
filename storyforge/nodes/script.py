"""Stage 1 — script.

Develops the user's brief into a plan (story_planner), writes the scenes from
that plan (crews/story_crew), then validates. The canned mock script is the
never-fail floor.

This node used to call a three-agent CrewAI crew first, catch the ImportError
that always fired (crewai is an optional extra and normally isn't installed),
emit a "fallback" event, and only then do the real work. That cost an exception
and a misleading yellow event on every single render, so the crew path is now
attempted only when crewai is genuinely importable.
"""
from __future__ import annotations

from ..config import format_spec, lane_budget
from ..crews.story_crew import generate_script, mock_script
from ..llm import select_model
from ..schema import (clamp_heroes, ensure_disclosure, normalize_script,
                      validate_script)
from ..utils import log
from .common import add_fallback, progress


def _finalize(raw: dict, hero_budget: tuple[int, int],
              scene_bounds: tuple[int, int]) -> dict:
    """Normalize + validate an LLM script. Raises ValueError if unsalvageable."""
    script = normalize_script(raw, hero_budget, scene_bounds)
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

    # The narrator's speed changes how many words fill a given runtime, so the
    # chosen style has to be resolved BEFORE any length maths happens.
    from .voice import VOICE_STYLES, resolve_style
    style_name, style = resolve_style(state, cfg)
    ratio = style["speed"] / VOICE_STYLES["adam"]["speed"]
    cfg = {**cfg, "voice_speed": float(cfg.get("voice_speed", 1.18) or 1.18) * ratio}
    state["voice_style"] = style_name

    model_info = state.get("model_info") or select_model(mock)
    log("info", f"LLM: {model_info['reason']}")

    script = None
    source = None
    plan = None
    t = None

    if not mock and model_info["backend"] != "mock":
        progress(state, "script", "developing the idea into a story plan")
        try:
            # Visual prompts are only worth generating when something will
            # actually render them. With gameplay backgrounds (the default look)
            # the visual comes from the user's clips, so asking the model for
            # detailed image prompts burns output tokens nobody reads.
            want_visuals = _visuals_needed(cfg, mock)
            script_raw, plan, t = generate_script(
                cfg, topic, hero_budget, model_info,
                requested_seconds=state.get("requested_seconds"),
                voice_style=style_name, want_visuals=want_visuals,
            )
            script = _finalize(script_raw, hero_budget, (t["scene_lo"], t["scene_hi"]))
            source = "plan"
        except Exception as exc:  # noqa: BLE001
            add_fallback(state, "script", f"planned generation failed ({exc}); using canned script")

    if script is None:
        raw = mock_script(cfg, lane, topic, hero_budget)
        n = len(raw.get("scenes") or []) or 8
        scene_bounds = format_spec(cfg)["scene_bounds"]
        relaxed = (min(scene_bounds[0], n), max(scene_bounds[1], n))
        script, source = _finalize(raw, hero_budget, relaxed), "mock"

    if source == "plan":
        try:
            from ..story_memory import remember
            remember(script.get("title", ""), script.get("hook", ""))
        except Exception as exc:  # noqa: BLE001
            log("warn", f"could not record story memory: {exc}")

    heroes = sum(1 for s in script["scenes"] if s.get("hero"))
    shape = (plan or {}).get("structure", "—")
    progress(state, "script",
             f"script ready via {source}: '{script['title']}' "
             f"({len(script['scenes'])} scenes, {heroes} hero, shape: {shape})")
    out = {"script": script, "script_source": source,
           "hero_budget": list(hero_budget), "model_info": model_info}
    if plan:
        out["plan"] = plan
    if t:
        # The voice stage refits real audio against these numbers.
        out["targets"] = t
    return out


def _visuals_needed(cfg: dict, mock: bool) -> bool:
    """True when some provider will actually consume `image_prompt`.

    Gameplay clips ignore it entirely (they just cut a random section), so when
    clips are present and no AI-video/stock provider is configured, generating
    prompts is pure waste.
    """
    try:
        from ..providers import BackgroundProvider, StockProvider
        if mock:
            return False
        if StockProvider(cfg).available():
            return True
        return not BackgroundProvider(cfg).available()
    except Exception:  # noqa: BLE001
        return True
