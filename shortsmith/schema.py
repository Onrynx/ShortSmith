"""Script JSON schema + validator (the contract the Critic must satisfy).

Schema:
{ "title": str(<70), "hook": str, "scenes":[ {"narration":str,
  "image_prompt":str, "hero":bool} ] (8-15), "description":str, "tags":[str](8-12) }

Rules enforced:
- scene count within the caller's bounds; each scene has non-empty narration.
- scene[0].narration == hook (spoken hook opens the video).
- hero count within the render budget.
- title < 70 chars; 8..12 tags.

Scenes also carry optional planning metadata (`purpose`, `planned_seconds`) set by
the planner. Nothing here may drop it: the timing/revision stages need to know
what a scene is FOR before deciding whether it can be shortened.

`validate_script` is strict and returns clear errors. `normalize_script` is a
lenient repair pass (clamp hero count, truncate title, coerce types, ensure the
disclosure line) used by the script node before final validation so a slightly
non-conforming LLM output is salvaged rather than thrown away.
"""
from __future__ import annotations

from typing import Any

DISCLOSURE_LINE = "This video contains AI-generated narration and imagery."

MIN_SCENES = 8
MAX_SCENES = 15
MIN_TAGS = 8
MAX_TAGS = 12
MAX_TITLE = 70


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def validate_script(data: Any, hero_budget: tuple[int, int] = (3, 15),
                    scene_bounds: tuple[int, int] = (MIN_SCENES, MAX_SCENES)) -> list[str]:
    """Return a list of human-readable errors. Empty list == valid.

    scene_bounds lets Shorts (e.g. 5-8 scenes) and long-form (8-15) share this
    validator.
    """
    lo_s, hi_s = scene_bounds
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["script is not a JSON object"]

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        errs.append("title missing or empty")
    elif len(title) >= MAX_TITLE:
        errs.append(f"title must be < {MAX_TITLE} chars (got {len(title)})")

    hook = data.get("hook")
    if not isinstance(hook, str) or not hook.strip():
        errs.append("hook missing or empty")

    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        errs.append("scenes missing or not a list")
        scenes = []
    else:
        if not (lo_s <= len(scenes) <= hi_s):
            errs.append(f"scenes count must be {lo_s}-{hi_s} (got {len(scenes)})")
        hero_count = 0
        for i, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                errs.append(f"scene {i} is not an object")
                continue
            if not isinstance(sc.get("narration"), str) or not sc["narration"].strip():
                errs.append(f"scene {i} narration missing/empty")
            if not isinstance(sc.get("image_prompt"), str) or not sc["image_prompt"].strip():
                errs.append(f"scene {i} image_prompt missing/empty")
            if not isinstance(sc.get("hero"), bool):
                errs.append(f"scene {i} hero must be a bool")
            elif sc["hero"]:
                hero_count += 1
        lo, hi = hero_budget
        if scenes and not (lo <= hero_count <= hi):
            errs.append(f"hero scene count {hero_count} outside lane budget {lo}-{hi}")

    # scene 1 narration must equal the hook
    if isinstance(scenes, list) and scenes and isinstance(hook, str):
        first = scenes[0].get("narration", "") if isinstance(scenes[0], dict) else ""
        if _norm_ws(first) != _norm_ws(hook):
            errs.append("scene 1 narration must equal the hook")

    desc = data.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errs.append("description missing or empty")

    tags = data.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        errs.append("tags must be a list of non-empty strings")
    elif not (MIN_TAGS <= len(tags) <= MAX_TAGS):
        errs.append(f"tags count must be {MIN_TAGS}-{MAX_TAGS} (got {len(tags) if isinstance(tags, list) else 0})")

    return errs


def clamp_heroes(scenes: list[dict], hero_budget: tuple[int, int]) -> list[dict]:
    """Force hero count into [lo, hi]. Excess heroes (later scenes) -> stock;
    if too few, promote the most 'visual-looking' non-hero scenes."""
    lo, hi = hero_budget
    heroes = [i for i, s in enumerate(scenes) if s.get("hero")]
    if len(heroes) > hi:
        for i in heroes[hi:]:
            scenes[i]["hero"] = False
    elif len(heroes) < lo:
        need = lo - len(heroes)
        # promote scenes with the longest image prompts (usually the most cinematic)
        candidates = sorted(
            [i for i, s in enumerate(scenes) if not s.get("hero")],
            key=lambda i: len(scenes[i].get("image_prompt", "")),
            reverse=True,
        )
        for i in candidates[:need]:
            scenes[i]["hero"] = True
    return scenes


def ensure_disclosure(description: str) -> str:
    description = (description or "").rstrip()
    if DISCLOSURE_LINE.lower() not in description.lower():
        description = (description + "\n\n" + DISCLOSURE_LINE).strip()
    return description


def normalize_script(data: Any, hero_budget: tuple[int, int],
                     scene_bounds: tuple[int, int] = (MIN_SCENES, MAX_SCENES)) -> dict:
    """Best-effort repair of an LLM script so it can pass validation.

    Never raises — returns something dict-shaped. The caller still runs
    validate_script afterwards and falls back to the canned script on failure.
    """
    hi_s = scene_bounds[1]
    if not isinstance(data, dict):
        data = {}
    out: dict[str, Any] = {}

    out["title"] = _norm_ws(str(data.get("title", "Untitled")))[: MAX_TITLE - 1] or "Untitled Story"

    scenes_in = data.get("scenes")
    scenes: list[dict] = []
    if isinstance(scenes_in, list):
        for sc in scenes_in:
            if not isinstance(sc, dict):
                continue
            narr = _norm_ws(str(sc.get("narration", "")))
            if not narr:
                continue                      # no words = nothing to say, drop it
            # A scene with narration but no image_prompt used to be DELETED here,
            # silently throwing away good writing (and shortening the video, which
            # then triggered expensive "make it longer" passes). The visual is
            # recoverable; the narration is not — so synthesise one instead.
            img = _norm_ws(str(sc.get("image_prompt", "")))
            if not img:
                img = _norm_ws(str(sc.get("visual", ""))) or "cinematic vertical scene"
            keep = {
                "narration": narr,
                "image_prompt": img,
                "hero": bool(sc.get("hero", False)),
            }
            # Carry planning metadata through — later stages use it to know what
            # each scene is FOR when fitting duration or choosing what to trim.
            for k in ("purpose", "planned_seconds"):
                if sc.get(k) is not None:
                    keep[k] = sc[k]
            scenes.append(keep)

    # hook: prefer explicit hook, else first scene narration
    hook = _norm_ws(str(data.get("hook", "")))
    if not hook and scenes:
        hook = scenes[0]["narration"]
    out["hook"] = hook

    # scene 1 narration must equal hook
    if scenes:
        scenes[0]["narration"] = hook or scenes[0]["narration"]
        if not out["hook"]:
            out["hook"] = scenes[0]["narration"]

    # clamp scene count to the format's max if we have enough material
    if len(scenes) > hi_s:
        scenes = scenes[:hi_s]
    out["scenes"] = scenes
    if scenes:
        out["scenes"] = clamp_heroes(scenes, hero_budget)

    out["description"] = ensure_disclosure(str(data.get("description", "")))

    tags = data.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags = [_norm_ws(str(t)) for t in tags if str(t).strip()]
    # pad/trim to [MIN_TAGS, MAX_TAGS]
    generic = ["documentary", "history", "mystery", "story", "cinematic",
               "explained", "lore", "facts", "narrated", "educational",
               "unsolved", "deep dive"]
    gi = 0
    while len(tags) < MIN_TAGS and gi < len(generic):
        if generic[gi] not in tags:
            tags.append(generic[gi])
        gi += 1
    out["tags"] = tags[:MAX_TAGS]

    return out
