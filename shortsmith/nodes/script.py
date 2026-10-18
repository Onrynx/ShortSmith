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

            def _published_plan(p: dict) -> None:
                """Surface the concept the moment it exists, so the bot can say
                what this video is actually about while it's still writing."""
                progress(state, "script",
                         f"writing: {str(p.get('logline',''))[:110]}",
                         extra={"logline": p.get("logline"),
                                "structure": p.get("structure"),
                                "beats": len(p.get("beats") or [])})

            def _published_progress(info: dict) -> None:
                progress(state, "script",
                         f"writing scene {info['scenes_written']}/{info['scenes_total']} "
                         f"({info['words_written']} words)",
                         extra=info)

            script_raw, plan, t = generate_script(
                cfg, topic, hero_budget, model_info,
                requested_seconds=state.get("requested_seconds"),
                voice_style=style_name, want_visuals=want_visuals,
                on_plan=_published_plan, on_progress=_published_progress,
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

    # Explainers get their facts checked. Stories do not need it — nobody is
    # misinformed by a made-up argument between flatmates — and the check costs
    # two model calls per claim, so it is spent only where it buys something.
    findings = _fact_check(state, script, plan, model_info, source)

    heroes = sum(1 for s in script["scenes"] if s.get("hero"))
    shape = (plan or {}).get("structure", "—")
    progress(state, "script",
             f"script ready via {source}: '{script['title']}' "
             f"({len(script['scenes'])} scenes, {heroes} hero, shape: {shape})")
    out = {"script": script, "script_source": source,
           "hero_budget": list(hero_budget), "model_info": model_info}
    if findings:
        out["fact_check"] = findings
    if plan:
        out["plan"] = plan
    if t:
        # The voice stage refits real audio against these numbers.
        out["targets"] = t
    return out


def _fact_check(state: dict, script: dict, plan: dict | None,
                model_info: dict, source: str) -> dict | None:
    """Verify an explainer's claims and correct the ones that are clearly wrong.

    A 14B model states false things with complete confidence — a real run of
    ours asserted "The Earth rotates east to west" inside an otherwise sound
    explanation. The check is a second opinion from the same model, asked one
    bare claim at a time and asked twice, which catches a surprising amount of
    exactly that. It is not infallible and is not presented as such: only
    agreed-wrong claims are corrected, and everything uncertain is shown to the
    user rather than quietly changed.
    """
    if source != "plan" or state.get("mock"):
        return None
    if (plan or {}).get("brief_kind") not in ("factual", "historical"):
        return None

    from ..model_profiles import profile_for
    prof = profile_for(model_info.get("model", ""))
    # On a slow machine this is minutes of extra wall clock, so check fewer
    # claims rather than skipping the pass — a wrong fact is worse than a wait.
    limit = {"full": 8, "balanced": 5}.get(prof.get("quality_passes"), 3)

    progress(state, "script", f"checking the facts ({limit} claims at most)")
    try:
        from ..factcheck import apply_corrections, check_script
        findings = check_script(script, model_info, limit=limit,
                                timeout=prof.get("timeout", 300))
        fixed = apply_corrections(script, findings, model_info,
                                  timeout=prof.get("timeout", 300))
    except Exception as exc:  # noqa: BLE001
        add_fallback(state, "script", f"fact-check did not run ({exc})")
        return None

    findings["corrected"] = fixed
    n_wrong, n_unsure = len(findings["wrong"]), len(findings["unsure"])
    if fixed:
        progress(state, "script", f"corrected {fixed} inaccurate line(s)")
    if n_unsure:
        add_fallback(state, "script",
                     f"{n_unsure} claim(s) could not be verified either way")
    log("info", f"fact-check: {len(findings['claims'])} claims checked, "
                f"{n_wrong} wrong ({fixed} corrected), {n_unsure} unsure")
    return findings


def _visuals_needed(cfg: dict, mock: bool) -> bool:
    """True when some provider will actually consume `image_prompt`.

    Gameplay clips ignore it entirely (they just cut a random section), so when
    clips are present and no AI-video/stock provider is configured, generating
    prompts is pure waste.
    """
    try:
        from ..providers import BackgroundProvider
        if mock:
            return False
        return not BackgroundProvider(cfg).available()
    except Exception:  # noqa: BLE001
        return True
