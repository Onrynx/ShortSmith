"""Script generation — turn a story PLAN into the scenes that become the video.

The old version of this module asked one model call to invent a topic, structure
a story, write it, and hit a word count, all at once, then tried to rescue the
result with a full rewrite and two "make it longer" passes. That is why stories
came out formulaic and rushed: nothing had ever decided what THIS story should
look like, so the prompt's example arc became the arc every time.

Now the thinking happens first (story_planner.py) and this module writes to that
plan. Each beat arrives with its own job, its own concrete events and its own
time budget, so the writer's only task is prose — which is the task a small local
model is actually good at.

Three generators, tried in order by nodes/script.py:

    write_script(...)   # plan-driven, the real path
    mock_script(...)    # canned, deterministic, always works (mock/offline floor)
"""
from __future__ import annotations

import json
import os
import random
import re

from ..config import format_spec
from ..llm import complete
from ..model_profiles import profile_for
from ..story_doctrine import DOCTRINE, FACTUAL_CRAFT, violations
from ..timing import (estimate_seconds, load_calibration, plan_revisions,
                      scene_seconds, words_for_seconds, wpm_for)
from .. import story_memory
from ..utils import log

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> dict:
    """Pull the first balanced JSON object out of an LLM response."""
    if not text:
        raise ValueError("empty LLM output")
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON braces")


def word_count_of(script: dict) -> int:
    return sum(len(str(s.get("narration", "")).split())
               for s in script.get("scenes", []))


def targets(cfg: dict, hero_budget: tuple[int, int],
            requested_seconds: float | None = None,
            voice_style: str = "adam", model: str = "") -> dict:
    """Resolve what this run is aiming for — length, scenes, pace, calibration.

    `requested_seconds` is an explicit user request ("make it 45 seconds") and
    always wins over the channel's configured window. `model` pulls in that
    model's profile, so a smaller writer gets fewer scenes and more attempts at
    hitting the length rather than being asked for work it can't do.
    """
    spec = format_spec(cfg)
    lo_s, hi_s = spec["scene_bounds"]
    calibration = load_calibration(voice_style)
    wpm = wpm_for(spec.get("voice_speed", 1.18))
    prof = profile_for(model)
    scale = float(prof.get("scene_scale", 1.0))
    if scale < 1.0:
        # Fewer, slightly longer scenes: a small model writes one good scene
        # more reliably than two thin ones. Scale the floor FIRST — using the
        # unscaled floor in the ceiling's guard stops the range shrinking at all.
        lo_s = max(3, int(round(lo_s * scale)))
        hi_s = max(lo_s + 1, int(round(hi_s * scale)))

    if requested_seconds and requested_seconds > 0:
        target_seconds = float(requested_seconds)
        # An explicit short request must be allowed to shrink the scene count,
        # otherwise a 45s ask still gets a 16-scene skeleton and races.
        lo_s = max(3, min(lo_s, int(round(target_seconds / 9.0))))
        hi_s = max(lo_s + 1, min(hi_s, int(round(target_seconds / 5.0))))
        max_seconds = max(target_seconds * 1.15, target_seconds + 6)
    elif spec["format"] == "shorts" and not spec.get("exact_seconds") and spec.get("min_seconds"):
        target_seconds = random.randint(spec["min_seconds"],
                                        max(spec["min_seconds"], spec["target_seconds"]))
        max_seconds = spec["max_seconds"]
    else:
        target_seconds = spec["target_seconds"]
        max_seconds = spec["max_seconds"]

    scene_count = max(lo_s, min(hi_s, round(target_seconds / 11.0)))
    words = words_for_seconds(target_seconds, wpm, calibration, scene_count)
    return {
        "format": spec["format"], "vertical": spec["vertical"],
        "target_seconds": round(float(target_seconds), 1),
        "max_seconds": float(max_seconds),
        "wpm": round(wpm), "calibration": calibration,
        "approx_words": words, "min_words": int(words * 0.85),
        "scene_count": scene_count, "fiction": bool(cfg.get("fiction")),
        "story_mode": cfg.get("story_mode", "real_life_drama"),
        "scene_lo": lo_s, "scene_hi": hi_s,
        "hero_lo": hero_budget[0], "hero_hi": hero_budget[1],
        "requested": bool(requested_seconds),
        # model-dependent knobs, resolved once so every stage agrees
        "tier": prof["tier"], "fit_rounds": prof["fit_rounds"],
        "plan_detail": prof["plan_detail"], "llm_timeout": prof["timeout"],
        # set by the hardware scan, not by a fixed assumption about speed
        "machine_speed": prof.get("machine_speed"),
        "quality_passes": prof.get("quality_passes"),
        "always_continuity": prof.get("always_continuity", False),
    }


def _visual_style(cfg: dict, t: dict) -> str:
    v = "vertical 9:16 composition, " if t["vertical"] else ""
    return f"{v}{cfg.get('style', 'cinematic')}"


# --------------------------------------------------------------------------- #
# Plan-driven writing
# --------------------------------------------------------------------------- #
_SYS_WRITE = (
    "You are a narration writer for short-form video. You are given a story plan "
    "that has already been developed — the premise, the people, the shape and the "
    "job of each beat. You write the spoken words, in order, following that plan "
    "exactly. You write the way people actually talk. You never summarise the "
    "story, never narrate ABOUT it, and never add beats the plan did not ask for. "
    "You return strict JSON only."
)


def _beat_block(beats: list, start_index: int) -> str:
    """Render the beats of this chunk as explicit, individually-budgeted jobs.

    The word count is stated as a hard floor with a sentence count, because a
    small model handed "about 20 words" reliably writes 11 — it treats the number
    as a ceiling. Saying "at least N words, that is X sentences" fixes it.
    """
    out = []
    for i, b in enumerate(beats):
        words = int(b.get("words", 30) or 30)
        sentences = max(2, round(words / 12))
        out.append(
            f"BEAT {start_index + i + 1} — purpose: {b.get('purpose','develop')}\n"
            f"  LENGTH: write AT LEAST {words} spoken words for this beat "
            f"(roughly {sentences} full sentences). Shorter than that is a failure.\n"
            f"  what happens: {b.get('what_happens','')}\n"
            f"  the viewer should now understand/feel: {b.get('why_it_matters','')}\n"
            f"  on screen: {b.get('visual','')}"
        )
    return "\n".join(out)


def _pacing_note(beats: list) -> str:
    """Tell the writer, in words, what these particular beats are FOR."""
    purposes = [str(b.get("purpose", "")).lower() for b in beats]
    if any(p in ("establish", "context") for p in purposes):
        return ("These beats are doing SETUP. Do not rush them and do not "
                "escalate yet. Give the viewer the concrete, ordinary details "
                "that make this person and place real — one specific object, "
                "habit, name or line of dialogue is worth more than any amount "
                "of general description. The story earns its later turns here.")
    if any(p in ("turn", "reveal") for p in purposes):
        return ("This is where the story turns. Make sure the information that "
                "makes it land was already visible earlier — the viewer should "
                "feel it click, not feel ambushed. Let the moment breathe for a "
                "sentence instead of racing past it.")
    if any(p in ("resolve", "aftermath") for p in purposes):
        return ("This is the ending. Land it the way the plan says — do not "
                "reach for a rhetorical question unless the plan calls for one. "
                "An image, a consequence or a flat final statement is often "
                "stronger.")
    return ("Keep moving, but stay concrete. Every beat should add something the "
            "viewer did not already know.")


def write_script(plan: dict, cfg: dict, model_info: dict, t: dict, *,
                 want_visuals: bool = True, on_progress=None) -> dict:
    """Write the full script from a plan. Chunked, plan-faithful, continuity-safe.

    Chunking exists because small local models degrade over long generations, not
    because the story needs slicing: each call gets the whole plan for context but
    is only asked to write a few beats, which is a task they do reliably.
    """
    from ..story_planner import plan_summary

    beats = plan.get("beats") or []
    summary = plan_summary(plan)
    style = _visual_style(cfg, t)
    mode = t.get("story_mode", "real_life_drama")
    # An explainer must not be written to drama craft — that is what turned a
    # physics brief into a personal anecdote.
    factual = plan.get("brief_kind") in ("factual", "historical")
    rules = FACTUAL_CRAFT if factual else DOCTRINE

    opening_line = ""
    if plan.get("brief_kind") == "opening_line":
        opening_line = str(plan.get("brief", "")).strip()

    visual_field = (
        ',"image_prompt":"what is on screen — concrete, no text/logos/real people"'
        if want_visuals else "")
    visual_rule = (
        f"\nimage_prompt: describe the shot for beat, style: {style}. No text, no "
        "logos, no real people, no copyrighted characters."
        if want_visuals else "")

    base = f"""STORY PLAN (follow it — this is already decided):
{summary}

CHANNEL: {cfg.get('niche')}
NEVER include: {cfg.get('banned')}

{rules}

HOW TO WRITE IT:
Write the SPOKEN narration, in order, one entry per beat. Hit each beat's stated
purpose and use roughly its word budget — that budget is how the video's pacing
was designed, so a beat with more words is meant to breathe.
Write in the narrator's voice described in the plan. Do not write stage
directions, scene labels, or commentary about the story.

Reach for the specific, not the stock phrase. "She taught me the ropes", "my
heart sank", "sick to my stomach", "little did I know", "everything changed" are
what writing sounds like when nobody chose the words. Say the actual thing
instead: what was on the screen, what she said, what he did with his hands. One
precise detail beats three sentences of feeling-words.

Each beat moves to somewhere new. Do not re-tell the beat before it in different
words — if the previous line already said a letter arrived, this one is about
what the letter did, not that it arrived. Restating is the most common way these
stories waste their runtime.

The people in the plan are the only people in this story. Do not introduce a new
named character the plan never mentioned.{visual_rule}"""

    if opening_line:
        base += (f'\n\nTHE FIRST SPOKEN SENTENCE MUST BE EXACTLY, WORD FOR WORD:\n'
                 f'"{opening_line}"')

    # How many beats to write per request. A 14B model handles four at a time;
    # a 3B model handed four produces mush, so the profile shrinks the ask.
    CHUNK = profile_for(model_info.get("model", ""))["beats_per_call"]
    scenes: list = []
    meta: dict = {}

    for start in range(0, len(beats), CHUNK):
        group = beats[start:start + CHUNK]
        if scenes:
            so_far = "\n".join(f"{i+1}. {s.get('narration','')}"
                               for i, s in enumerate(scenes))
            continuity = (f"\n\nWHAT HAS ALREADY BEEN SAID (do not repeat, "
                          f"contradict or restart any of it):\n{so_far}\n"
                          "Continue seamlessly from the last line.")
            ask_meta = ""
        else:
            continuity = ""
            ask_meta = ('\nAlso return "title" (under 65 chars, no clickbait '
                        'punctuation spam), "description" (2 sentences) and '
                        '"tags" (8-12 lowercase tags).')

        need_words = sum(int(b.get("words", 30) or 30) for b in group)
        user = (base + continuity +
                f"\n\nWRITE THESE BEATS NOW:\n{_beat_block(group, start)}\n\n"
                f"{_pacing_note(group)}\n"
                f"These {len(group)} beats together must total AT LEAST "
                f"{need_words} spoken words. Count as you write.{ask_meta}\n\n"
                'Return ONLY strict JSON: {"scenes":[{"narration":"the spoken words"'
                + visual_field + "}]" + (', "title":"...","description":"...","tags":["..."]'
                                         if not scenes else "") + "}")
        try:
            part = extract_json(complete(_SYS_WRITE, user, model_info,
                                         json_mode=True, temperature=0.85,
                                         timeout=600))
        except Exception as exc:  # noqa: BLE001
            log("warn", f"beat chunk {start//CHUNK + 1} failed ({exc})")
            break

        if not meta:
            meta = {k: part.get(k) for k in ("title", "description", "tags")}
        got = [s for s in (part.get("scenes") or [])
               if isinstance(s, dict) and str(s.get("narration", "")).strip()]
        if not got:
            log("warn", f"beat chunk {start//CHUNK + 1} returned no scenes")
            continue
        # Carry the plan's purpose + visual onto the written scene so later
        # stages (timing, revision, canvas) still know what each scene is FOR.
        for j, sc in enumerate(got[:len(group)]):
            b = group[j] if j < len(group) else {}
            sc["purpose"] = b.get("purpose", "develop")
            sc["planned_seconds"] = b.get("seconds")
            if not sc.get("image_prompt"):
                vis = b.get("visual") or plan.get("setting") or ""
                sc["image_prompt"] = f"{vis}, {style}".strip(", ")
        scenes.extend(got[:len(group)])
        written = sum(len(str(s.get("narration", "")).split()) for s in scenes)
        log("info", f"wrote beats {start+1}-{start+len(got)} ({written} words so far)")
        if on_progress:
            try:
                on_progress({"scenes_written": len(scenes),
                             "scenes_total": len(beats),
                             "words_written": written,
                             "opening": scenes[0].get("narration", "")[:300]})
            except Exception:  # noqa: BLE001
                pass

    if not scenes:
        raise ValueError("writer produced no scenes")

    data = {**meta, "scenes": scenes}
    if not data.get("title"):
        data["title"] = str(plan.get("title_idea") or "Untitled Story")[:64]
    data["hook"] = scenes[0].get("narration", "")
    if opening_line:
        # The user's exact line is authoritative — restore it if the model drifted.
        data["scenes"][0]["narration"] = opening_line
        data["hook"] = opening_line
    return data


# --------------------------------------------------------------------------- #
# Surgical revision — fix length without wrecking the story
# --------------------------------------------------------------------------- #
_SYS_REVISE = (
    "You are a script editor. You tighten or develop specific scenes of a story "
    "to hit a runtime, without changing what happens. You never delete plot, "
    "never add new events, and never pad with filler. While you are rewriting a "
    "line you also replace stock phrases — 'my heart sank', 'hit me like a wave', "
    "'sick to my stomach', 'little did I know', 'everything changed' — with the "
    "specific thing that actually happened. You return strict JSON only."
)


def revise_for_duration(script: dict, revisions: dict, model_info: dict,
                        t: dict) -> dict:
    """Compress or expand only the scenes `timing.plan_revisions` selected.

    This is the alternative to the two blunt instruments this pipeline used
    before: asking for a whole new draft (expensive, loses the good version) and
    speeding the audio up (makes the narrator sound like a chipmunk).
    """
    action = revisions.get("action")
    marks = revisions.get("targets") or []
    if action not in ("compress", "expand") or not marks:
        return script

    scenes = script.get("scenes") or []
    jobs = []
    for m in marks:
        i = int(m["index"])
        if not (0 <= i < len(scenes)):
            continue
        cur_words = m.get("current_words") or len(str(scenes[i].get("narration", "")).split())
        # convert the seconds we need to move into a word target for that scene
        delta_words = int(m["seconds"] / 60.0 * t["wpm"] / max(t["calibration"], 0.1))
        goal = (cur_words - delta_words) if action == "compress" else (cur_words + delta_words)
        goal = max(8, goal)
        jobs.append({"index": i, "narration": scenes[i].get("narration", ""),
                     "purpose": scenes[i].get("purpose", ""), "target_words": goal})

    if not jobs:
        return script

    if action == "compress":
        how = ("Tighten each scene to about its target word count. Keep every "
               "plot fact, every name, and the emotional turn. Cut redundancy, "
               "throat-clearing and any sentence that restates the previous one. "
               "Combine two sentences into one where they carry the same beat. "
               "Do NOT delete events and do NOT summarise — it must still read as "
               "spoken storytelling.")
    else:
        how = ("Develop each scene to about its target word count. Add real "
               "substance that belongs there: a concrete detail, what the "
               "narrator noticed or felt, one line of dialogue, a beat of "
               "context that makes the next event land harder. Invent NO new "
               "events and do not contradict anything. Never pad with filler or "
               "restate what was already said.")

    # Revise in SMALL BATCHES. Handing a small local model a dozen scenes at once
    # reliably gets one or two back — a live run asked for ~10 expansions and got
    # exactly 1, leaving the video 30% short. Four at a time it does properly,
    # and smaller models need an even shorter list (see model_profiles).
    BATCH = profile_for(model_info.get("model", ""))["revision_batch"]
    updated = 0
    for b in range(0, len(jobs), BATCH):
        group = jobs[b:b + BATCH]
        user = (f"{how}\n\nEach scene below has a target_words. Rewrite ONLY the "
                f"narration of each, keeping its index. Return ALL "
                f"{len(group)} scenes.\n\n"
                + json.dumps({"scenes": group}, ensure_ascii=False)
                + '\n\nReturn ONLY: {"scenes":[{"index":0,"narration":"..."}]}')
        try:
            out = extract_json(complete(_SYS_REVISE, user, model_info,
                                        json_mode=True, temperature=0.6,
                                        timeout=600))
        except Exception as exc:  # noqa: BLE001
            log("warn", f"duration revision batch failed ({exc}); skipping batch")
            continue
        for item in out.get("scenes") or []:
            try:
                i = int(item.get("index"))
                new = str(item.get("narration", "")).strip()
            except Exception:
                continue
            if not new or not (0 <= i < len(scenes)):
                continue
            old_n = len(str(scenes[i].get("narration", "")).split())
            new_n = len(new.split())
            # only accept a change that actually moves in the requested direction
            if action == "compress" and new_n >= old_n:
                continue
            if action == "expand" and new_n <= old_n:
                continue
            scenes[i]["narration"] = new
            updated += 1

    if updated:
        script["scenes"] = scenes
        if scenes:
            script["hook"] = scenes[0].get("narration", script.get("hook", ""))
        log("info", f"duration revision: {action}ed {updated}/{len(jobs)} scene(s)")
    return script


def enforce_doctrine(script: dict, plan: dict, cfg: dict, model_info: dict,
                     t: dict) -> dict:
    """One targeted fix pass for hard-rule breaches (not a whole-story rewrite).

    Only the offending scenes are rewritten, so a good draft is never thrown away
    over a single bad line — which is what the old full-rewrite loop did.
    """
    mode = t.get("story_mode", "real_life_drama")
    factual = plan.get("brief_kind") in ("factual", "historical")
    grounded = not factual and mode != "lore"
    narrator = str(plan.get("narrator", "")).lower()
    first_person = (not factual) and (
        "first person" in narrator or "first-person" in narrator or not narrator)

    faults = violations(script, mode=mode, grounded=grounded,
                        first_person=first_person)
    dup = story_memory.duplicate_of(script)
    if dup:
        faults.append(f"this repeats an earlier video ('{dup}')")
    if not faults:
        return script

    log("warn", f"doctrine faults: {'; '.join(faults)[:160]}")
    scenes = script.get("scenes") or []
    if not scenes:
        return script

    user = ("The following story breaks these rules:\n- " + "\n- ".join(faults) +
            "\n\nRewrite ONLY the narration of the scenes that break them, keeping "
            "the same events, order and length. Change nothing else.\n\n"
            + json.dumps({"scenes": [{"index": i, "narration": s.get("narration", "")}
                                     for i, s in enumerate(scenes)]},
                         ensure_ascii=False)
            + '\n\nReturn ONLY: {"scenes":[{"index":0,"narration":"..."}]}')
    try:
        out = extract_json(complete(_SYS_REVISE, user, model_info, json_mode=True,
                                    temperature=0.75, timeout=600))
    except Exception as exc:  # noqa: BLE001
        log("warn", f"doctrine fix failed ({exc}); keeping draft")
        return script

    for item in out.get("scenes") or []:
        try:
            i = int(item.get("index"))
            new = str(item.get("narration", "")).strip()
        except Exception:
            continue
        if new and 0 <= i < len(scenes):
            scenes[i]["narration"] = new
    script["scenes"] = scenes
    if scenes:
        script["hook"] = scenes[0].get("narration", script.get("hook", ""))
    return script


def generate_script(cfg: dict, topic: str, hero_budget: tuple[int, int],
                    model_info: dict, *, requested_seconds: float | None = None,
                    voice_style: str = "adam", want_visuals: bool = True,
                    on_plan=None, on_progress=None) -> tuple:
    """Plan -> write -> fix -> length-fit. Returns (script, plan, targets).

    `on_plan`/`on_progress` let the caller publish live detail (the logline, the
    lines written so far) while this runs, so the bot can answer "what stage is
    it in, show me a sample" with something real instead of a stage name.
    """
    from ..story_planner import build_plan

    t = targets(cfg, hero_budget, requested_seconds, voice_style,
                model=model_info.get("model", ""))
    # Kokoro voice ids encode gender: am_* / bm_* are male, af_* / bf_* female.
    voice_id = str(cfg.get("voice", "am_adam"))
    narrator_gender = "female" if voice_id[:2] in ("af", "bf") else "male"
    plan = build_plan(
        topic, cfg, model_info,
        target_seconds=t["target_seconds"],
        scene_bounds=(t["scene_lo"], t["scene_hi"]),
        wpm=t["wpm"], calibration=t["calibration"],
        seed=story_memory.seed_prompt() if not topic else "",
        avoid=story_memory.avoid_list() if not topic else "",
        narrator_gender=narrator_gender,
    )
    if on_plan:
        try:
            on_plan(plan)
        except Exception:  # noqa: BLE001 — telemetry must never break a render
            pass
    script = write_script(plan, cfg, model_info, t, want_visuals=want_visuals,
                          on_progress=on_progress)
    script = enforce_doctrine(script, plan, cfg, model_info, t)
    # Only stage that re-reads the whole draft: catches contradictions
    # between distant scenes and characters the plan never had.
    from ..continuity import continuity_pass
    script = continuity_pass(script, plan, model_info, t["llm_timeout"],
                             always=t.get("always_continuity", False))

    # Length fit on the ESTIMATE now; the voice stage refits against real audio.
    # Iterate: one pass rarely closes a big gap, and estimates are free compared
    # with re-rendering audio later.
    # Smaller models move less per pass, so they're allowed more attempts to
    # land on the requested duration.
    est = 0.0
    for attempt in range(t["fit_rounds"]):
        scenes = script.get("scenes") or []
        est_each = [scene_seconds(s.get("narration", ""), t["wpm"], t["calibration"])
                    for s in scenes]
        est = round(sum(est_each), 2)
        rev = plan_revisions(scenes, est_each, t["target_seconds"],
                             t["max_seconds"], aggression=attempt)
        if rev.get("action") == "ok" or not rev.get("targets"):
            break
        before = est
        script = revise_for_duration(script, rev, model_info, t)
        after = estimate_seconds(script.get("scenes") or [], t["wpm"], t["calibration"])
        log("info", f"length fit {attempt+1}: {before:.0f}s -> {after:.0f}s "
                    f"(target {t['target_seconds']:.0f}s)")
        est = after
        # Only give up on a stalled pass once we are actually near the target.
        # Bailing while still far out was leaving videos 20% over length; the
        # next round is allowed to compress harder, so it is worth continuing.
        if abs(after - before) < 1.0:
            still_bad = plan_revisions(
                script.get("scenes") or [],
                [scene_seconds(x.get("narration", ""), t["wpm"], t["calibration"])
                 for x in script.get("scenes") or []],
                t["target_seconds"], t["max_seconds"],
                aggression=attempt + 1).get("severe")
            if not still_bad:
                break
        est = after
    log("info", f"script: {word_count_of(script)} words ≈ {est:.0f}s "
                f"(target {t['target_seconds']:.0f}s, {t['wpm']} wpm, "
                f"calib {t['calibration']:.2f})")
    return script, plan, t


# --------------------------------------------------------------------------- #
# 2) Mock canned scripts (deterministic; the never-fail floor)
# --------------------------------------------------------------------------- #
def _scenes(rows):
    return [{"narration": n, "image_prompt": p, "hero": h} for (n, p, h) in rows]


def _gaming_short() -> dict:
    s = "vertical 9:16 composition, epic concept art, dramatic fantasy environments, painterly, volumetric light"
    rows = [
        ("Every game world hides one catastrophe so old the characters only whisper "
         "about it.",
         f"A colossal shattered continent floating in a starlit void, glowing molten cracks "
         f"along its edges, {s}", True),
        ("Long ago the world was whole, held together by a great throne of pure light.",
         f"An immense throne of radiant crystal atop a mountain, beams of light holding up "
         f"a golden sky, {s}", True),
        ("Its guardians swore the sacred fire would never die. For a thousand years, it "
         "didn't.",
         f"Cloaked guardians in ash-grey armor standing vigil around a great flame, drifting "
         f"embers, {s}", True),
        ("Then one of them asked the forbidden question — what if the fire burned "
         "hotter? Ambition cracked the world.",
         f"A lone armored figure reaching toward a roaring sacred flame, face lit orange, "
         f"deep shadows, {s}", True),
        ("The lore never fully confirms what happened next, and that ambiguity is the "
         "whole point.",
         f"Torn glowing manuscript pages scattered across a dark stone floor, flickering "
         f"runes, {s}", False),
        ("Every dungeon you explore is a shard of that broken throne. So which guardian "
         "would you have been?",
         f"A lone adventurer silhouetted on a cliff over a shattered floating world at "
         f"twilight, twin moons, {s}", True),
    ]
    return {
        "title": "The Catastrophe That Secretly Shaped This Game World",
        "hook": rows[0][0], "scenes": _scenes(rows),
        "description": ("Every game world hides a wound at its center. The cinematic story "
                        "of the Sundering. Original lore; no trademarked characters. #shorts"),
        "tags": ["game lore", "shorts", "gaming", "lore explained", "hidden story",
                 "rpg lore", "dark fantasy", "worldbuilding", "gaming shorts", "lore"],
    }


def _gaming_long() -> dict:
    style = "epic concept art, dramatic fantasy environments, painterly, volumetric light"
    rows = [
        ("Every great game world has a wound at its center — a catastrophe so old the "
         "characters only whisper about it. This is the story of the Sundering, and why the "
         "world can never truly heal.",
         f"A colossal shattered continent floating in a starlit void, glowing cracks of "
         f"molten light along its broken edges, {style}", True),
        ("Long before the first quest, the world was whole, held together by a great "
         "throne of light said to keep the sky from falling.",
         f"An immense throne of radiant crystal atop a mountain, beams of light holding up a "
         f"golden sky, {style}", True),
        ("Its keepers were the Ashen Wardens — an order sworn to never let the throne's "
         "fire go out. For a thousand years, it never did.",
         f"Cloaked guardians in ash-grey armor standing vigil around a great flame, embers "
         f"drifting, {style}", True),
        ("Then one Warden asked a forbidden question: what would happen if the fire burned "
         "hotter? Ambition, as always, was the first crack.",
         f"A lone armored figure reaching toward a roaring sacred flame, face lit orange, "
         f"shadows deep, {style}", True),
        ("The lore fragments you can find in-game disagree about what came next. Some say "
         "betrayal, some say an honest mistake. The game never fully confirms it.",
         f"Torn glowing manuscript pages scattered across a dark stone floor, arcane runes "
         f"flickering, {style}", False),
        ("What every account agrees on is the result: the throne cracked, the fire escaped, "
         "and the world broke apart into the floating shards adventurers now call home.",
         f"A world splitting into vast floating islands, waterfalls pouring into an endless "
         f"abyss below, {style}", True),
        ("The Ashen Wardens did not die. They were unmade — scattered as hollow echoes "
         "that still guard ruins no one remembers building.",
         f"A ghostly hollow knight standing in an overgrown ruined hall, faint blue glow in "
         f"empty armor, {style}", True),
        ("This is why the world feels haunted. Every dungeon is a piece of the throne. Every "
         "boss is a Warden who forgot they lost.",
         f"An ancient overgrown dungeon entrance glowing with faint runic light, mist "
         f"pooling, {style}", False),
        ("And the throne itself? The lore hides it well. A few cryptic lines suggest a "
         "fragment still burns somewhere, waiting for someone to make the same choice again.",
         f"A single ember of golden light floating in a dark cavern, impossibly bright against "
         f"the black, {style}", True),
        ("That is the quiet genius of great game lore. It does not hand you the ending. It "
         "leaves an open loop and trusts you to carry it out of the game.",
         f"A lone adventurer silhouetted on a cliff edge overlooking the shattered floating "
         f"world at sunset, {style}", False),
        ("So the next time a ruin feels older than the game around it, remember the "
         "Sundering — and ask yourself which Warden you would have been.",
         f"A vast twilight vista of floating ruins under twin moons, contemplative and "
         f"immense, {style}", False),
    ]
    return {
        "title": "The Catastrophe That Secretly Shaped This Whole Game World",
        "hook": rows[0][0], "scenes": _scenes(rows),
        "description": ("Every game world hides a wound at its center. The cinematic story of "
                        "the Sundering. Original lore inspired by great RPGs; no trademarked "
                        "characters depicted."),
        "tags": ["game lore", "video game lore", "gaming", "lore explained", "hidden story",
                 "rpg lore", "dark fantasy", "worldbuilding", "gaming mysteries", "deep dive"],
    }


def _gossip_story() -> dict:
    s = "colorful dramatic slice-of-life, expressive faces, cinematic lighting, 9:16"
    rows = [
        ("My sister showed up to my wedding in a white dress — and that was the SECOND "
         "worst thing she did that day.",
         f"A bride frozen at the altar as a woman in white walks in, guests gasping, {s}", True),
        ("For context, Maya had spent our whole lives one step ahead of me. Same college, "
         "same job offers, and somehow always dating whoever I liked first.",
         f"Two sisters as kids, one always a step ahead, warm nostalgic tone, {s}", True),
        ("So when I got engaged to Daniel, I should have known she couldn't just be happy "
         "for me. But nothing prepared me for what she was planning.",
         f"A woman looking at an engagement ring, another watching jealously from a doorway, {s}",
         True),
        ("The white dress was the opening move. Everyone stared. My mom went pale. And Maya "
         "just smiled at me like she'd already won.",
         f"A jealous woman in white smiling coldly across a wedding hall, {s}", True),
        ("Then during the toasts, she grabbed the microphone. She said she had something the "
         "bride 'needed to hear before it was too late.'",
         f"A woman seizing a microphone at a wedding, crowd tense, dramatic spotlight, {s}", True),
        ("She pulled out her phone and started reading texts — texts she claimed were between "
         "Daniel and another woman. The room went dead silent.",
         f"A phone held up showing messages, shocked wedding guests blurred behind, {s}", True),
        ("Here's what she didn't know. Those texts weren't Daniel's. They were from a fake "
         "number I had set up three weeks earlier, because I'd caught her going through his phone.",
         f"A close-up of two phones, one labeled a decoy, clever reveal lighting, {s}", False),
        ("I'd fed her just enough to make her think she'd found something. And she took the "
         "bait in front of two hundred people.",
         f"A calm bride watching a plan unfold, subtle knowing smile, {s}", True),
        ("So when she finished her little speech, I walked up, took the mic, and asked her to "
         "read the very last message out loud. The one she'd skipped.",
         f"A bride confidently taking the microphone back, crowd leaning in, {s}", True),
        ("It said: 'Maya, if you're reading this at the wedding, you just proved everything.' "
         "Her face crumbled. My mom finally understood. And Daniel just started laughing.",
         f"A woman's confident expression collapsing into shock, gasping crowd, {s}", True),
        ("Maya left before the cake. We haven't spoken since. But honestly? Best wedding gift "
         "she could have given me. Would you have forgiven her — or done exactly what I did?",
         f"An empty chair at a wedding table, celebration continuing warmly behind it, {s}", False),
    ]
    return {
        "title": "My Sister Wore White to My Wedding — Big Mistake",
        "hook": rows[0][0], "scenes": _scenes(rows),
        "description": ("Storytime: she tried to ruin my wedding and exposed only herself. "
                        "Would you have forgiven her? #storytime #drama"),
        "tags": ["storytime", "drama", "gossip", "reddit stories", "family drama",
                 "wedding drama", "story", "revenge", "shorts", "storytime shorts"],
    }


def mock_script(cfg: dict, lane: str, topic: str, hero_budget: tuple[int, int]) -> dict:
    """Deterministic canned script keyed by channel + format; always valid material."""
    key = cfg.get("key", "")
    spec = format_spec(cfg)
    # 6-scene short variants only fit channels whose bounds allow them; else the
    # 11-scene variants (valid up to [9,14]).
    shorts = spec["format"] == "shorts" and spec["scene_bounds"][0] <= 6
    if key == "gossip_stories" or cfg.get("fiction") and "gossip" in cfg.get("niche", "").lower():
        data = _gossip_story()
    elif key == "gaming_lore" or "lore" in cfg.get("niche", "").lower():
        data = _gaming_short() if shorts else _gaming_long()
    else:
        data = _gossip_story()
    if topic:
        log("info", f"mock script: topic '{topic}' noted (canned content used offline)")
    return data
