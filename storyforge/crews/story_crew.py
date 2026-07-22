"""The CrewAI creative crew — Scout -> Scribe -> Critic — plus its fallbacks.

This module is the single "creative" node inside the LangGraph assembly. It runs
on whatever LLM backend was selected (Odysseus preferred, then Ollama, Gemini, or
the canned mock). It exposes three script generators the `script` node tries in
order:

    run_creative_crew(...)   # CrewAI collaborative crew (needs crewai + a backend)
    single_shot_script(...)  # one LLM call (needs a backend, no crewai)
    mock_script(...)         # canned, deterministic, always works (mock mode)

Format-aware: StoryForge defaults to YouTube SHORTS (vertical 9:16, <=60s, punchy),
but the same generators produce long-form when a channel sets format: long.
"""
from __future__ import annotations

import json
import os
import random
import re

from ..config import format_spec
from ..llm import complete, make_crew_llm
from ..story_doctrine import DOCTRINE, violations
from .. import story_memory
from ..utils import log

# Disable CrewAI telemetry so an offline machine never blocks on it.
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


def _targets(cfg: dict, hero_budget: tuple[int, int]) -> dict:
    spec = format_spec(cfg)
    lo_s, hi_s = spec["scene_bounds"]
    # Random length per run inside the channel's window, so uploads never feel
    # templated (e.g. 114-144s = 1.9-2.4 min).
    if spec["format"] == "shorts" and not spec.get("exact_seconds") and spec.get("min_seconds"):
        target_seconds = random.randint(spec["min_seconds"],
                                        max(spec["min_seconds"], spec["target_seconds"]))
    else:
        target_seconds = spec["target_seconds"]
    # CRITICAL: budget words at the pace the narration is ACTUALLY delivered.
    # Kokoro speaks ~150 wpm at 1.0x, so voice_speed 1.18 => ~177 wpm. Budgeting
    # at 150 made every video finish ~15% short of target.
    wpm = 150.0 * float(spec.get("voice_speed", 1.18) or 1.18)
    words = int(target_seconds / 60 * wpm)
    per_scene = 28 if spec["format"] == "shorts" else 55
    scene_count = max(lo_s, min(hi_s, round(words / per_scene)))
    return {
        "format": spec["format"], "vertical": spec["vertical"],
        "target_seconds": target_seconds, "max_seconds": spec["max_seconds"],
        "wpm": round(wpm),
        # tight floor: a short script is the #1 cause of stubby videos
        "approx_words": words, "min_words": int(words * 0.9),
        "scene_count": scene_count, "fiction": bool(cfg.get("fiction")),
        "story_mode": cfg.get("story_mode", "real_life_drama"),
        "scene_lo": lo_s, "scene_hi": hi_s,
        "hero_lo": hero_budget[0], "hero_hi": hero_budget[1],
    }


# Story-craft rules injected into every script prompt. This is what separates
# "generic AI slop" from a hook that holds retention for 1-2 minutes.
CRAFT = """STORYTELLING RULES (non-negotiable):
- Pick ONE specific story with a NAMED subject (a person, ship, city, battle,
  artifact, glitch, guild...) — never a vague theme. Specifics sell: names,
  dates, numbers, places in almost every scene.
- HOOK (scene 1, <=20 words): drop the listener INTO the strangest fact of the
  story mid-action, creating an information gap they must close. No throat-clearing.
- BANNED phrases: "imagine", "what if I told you", "in this video", "let's dive
  in", "join me", "have you ever wondered", "little did they know", "to this day"
  as filler, and any generic listicle framing.
- Arc: hook -> setup (who/where/when, concrete) -> escalation (each scene adds a
  NEW surprising fact or reversal) -> twist that reframes everything -> ending
  that answers the hook BUT opens one final question for the comments.
- Short punchy sentences. Cliffhang scene endings. Vary rhythm. Read it aloud in
  your head: if a sentence sounds like a textbook, rewrite it like you're telling
  a friend a secret at 2am."""


def _schema_spec(t: dict) -> str:
    vertical = ", VERTICAL 9:16 composition" if t["vertical"] else ""
    return (
        'Return ONLY strict JSON, no prose, matching exactly:\n'
        '{ "title": "<70 chars", "hook": "string", '
        '"scenes": [ {"narration":"1-2 short sentences","image_prompt":"detailed visual",'
        '"hero": true|false} ], "description":"string", "tags":["8-12 tags"] }\n'
        f"Rules: {t['scene_lo']}-{t['scene_hi']} scenes. scenes[0].narration MUST equal hook. "
        f"image_prompt must contain NO text, NO logos, NO real people, NO copyrighted "
        f"characters{vertical}. Mark {t['hero_lo']}-{t['hero_hi']} scenes hero=true."
    )


def _format_note(t: dict) -> str:
    fiction = ("\nFICTION: the story is INVENTED — but it must feel like a real "
               "thing that happened to a real person. Never real people or brands."
               if t.get("fiction") else "")
    # The doctrine governs every story channel; 'lore' channels keep the old craft.
    rules = CRAFT if t.get("story_mode") == "lore" else DOCTRINE
    if t["format"] == "shorts":
        return (
            f"This is a YOUTUBE SHORT: vertical 9:16, {t['target_seconds']}s of "
            f"narration (HARD MINIMUM {t['min_words']} spoken words total, aim "
            f"~{t['approx_words']}; under {t['max_seconds']}s). Each scene = 2-3 "
            "full spoken sentences, 25-35 words per scene.\n\n" + rules + fiction
        )
    return (
        f"This is a long-form video: ~{t['target_seconds']//60} minutes at ~150 wpm "
        f"(~{t['approx_words']} words).\n\n" + rules + fiction
    )


# --------------------------------------------------------------------------- #
# 1) CrewAI collaborative crew
# --------------------------------------------------------------------------- #
def run_creative_crew(cfg: dict, lane: str, topic: str,
                      hero_budget: tuple[int, int], model_info: dict) -> dict:
    """Scout -> Scribe -> Critic. Raises if crewai/backend unavailable or fails."""
    from crewai import Agent, Crew, Process, Task  # type: ignore

    llm = make_crew_llm(model_info)
    if llm is None:
        raise RuntimeError("no CrewAI LLM available")

    t = _targets(cfg, hero_budget)
    niche, style, banned = cfg.get("niche"), cfg.get("style"), cfg.get("banned")
    topic_line = (f"The user requested this topic: '{topic}'. Honor it." if topic
                  else "Choose the single most curiosity-driving topic yourself.")

    scout = Agent(
        role=f"Scout for a '{niche}' YouTube channel",
        goal=("Pick ONE high-curiosity topic with a built-in open loop — a question "
              "the viewer NEEDS answered. Respect the banned list."),
        backstory=("A relentless researcher who knows which untold story makes someone "
                   "stop scrolling. You never pick anything on the banned list."),
        llm=llm, allow_delegation=False, verbose=False,
    )
    scribe = Agent(
        role="Scribe — cinematic narration writer",
        goal=("Write spoken-word narration as scenes. Never invent facts; when the "
              "truth is uncertain, say so and make THAT the hook."),
        backstory=("A documentary narrator's ghostwriter who writes image prompts a "
                   "video model can render with no text or logos."),
        llm=llm, allow_delegation=False, verbose=False,
    )
    critic = Agent(
        role="Critic — ruthless retention editor",
        goal=("Cut weak sentences, verify the hook opens a loop fast, flag "
              "unverifiable claims for softening, and output STRICT JSON."),
        backstory=("You have edited a thousand viral scripts. You are merciless. "
                   "You reject anything that smells fantasy, AI-written, or slow."),
        llm=llm, allow_delegation=False, verbose=False,
    )

    scout_task = Task(
        description=(f"Niche: {niche}. Angle: {cfg.get('angle')}. {topic_line}\n"
                     f"BANNED (never use): {banned}.\n{_format_note(t)}\n"
                     "Deliver: the topic, why it grips, and the ONE open-loop question."),
        expected_output="A topic brief: topic, hook-question, why it grips.",
        agent=scout,
    )
    scribe_task = Task(
        description=(f"{_format_note(t)}\n"
                     f"Write ~{t['scene_count']} scenes ({t['scene_lo']}-{t['scene_hi']}). "
                     f"Each scene: narration (1-2 short sentences) + image_prompt (cinematic, "
                     f"style='{style}', "
                     f"{'vertical 9:16, ' if t['vertical'] else ''}NO text/logos/real people/"
                     f"copyrighted characters) + hero bool. "
                     f"Mark {t['hero_lo']}-{t['hero_hi']} scenes hero=true. "
                     "Scene 1 narration MUST be the hook. Be honest about uncertainty."),
        expected_output="All scenes with narration, image_prompt and hero flags.",
        agent=scribe, context=[scout_task],
    )
    critic_task = Task(
        description=("Ruthlessly edit for retention. Ensure the hook opens a loop "
                     "immediately. Soften unverifiable claims ('historians believe', "
                     f"'the records suggest'). Cap hero scenes to {t['hero_lo']}-{t['hero_hi']}.\n"
                     + _schema_spec(t)),
        expected_output="ONLY the strict JSON object. No commentary.",
        agent=critic, context=[scout_task, scribe_task],
    )

    crew = Crew(agents=[scout, scribe, critic],
                tasks=[scout_task, scribe_task, critic_task],
                process=Process.sequential, verbose=False)
    result = crew.kickoff()
    raw = getattr(result, "raw", None) or str(result)
    return extract_json(raw)


# --------------------------------------------------------------------------- #
# 2) Single-shot LLM (no CrewAI)
# --------------------------------------------------------------------------- #
def single_shot_script(cfg: dict, lane: str, topic: str,
                       hero_budget: tuple[int, int], model_info: dict) -> dict:
    t = _targets(cfg, hero_budget)
    # A user-supplied starter is authoritative. If it reads like an opening line
    # (a first-person sentence) it BECOMES the hook; otherwise it's the subject.
    topic = (topic or "").strip()
    looks_like_line = bool(re.match(r"^(my|i|he|she|they|we|our)\b", topic, re.I)) and len(topic.split()) >= 4
    if topic and looks_like_line:
        topic_line = (f'STARTER LINE (use this EXACT sentence as scenes[0].narration and '
                      f'as "hook", then build the whole story out of it): "{topic}"')
    elif topic:
        topic_line = (f"REQUIRED SUBJECT — the story must be about: {topic}. "
                      "Write an original first-person story on exactly this.")
    else:
        topic_line = "Choose the single most curiosity-driving topic yourself."
    lore = t.get("story_mode") == "lore"
    system = (
        "You are a three-in-one YouTube team: a Scout (topic), a Scribe (narration), "
        "and a Critic (ruthless retention editor)."
        if not lore else
        "You are a three-in-one YouTube team: a Scout (topic), a Scribe (cinematic "
        "narration), and a Critic (ruthless retention editor). You never invent facts; "
        "when uncertain you say so and make that the hook."
    )
    # A fresh random premise + the do-not-repeat list keep every video original.
    # A user-supplied topic REPLACES the random seed (never fight the user).
    fresh = "" if (lore or topic) else f"{story_memory.seed_prompt()}\n{story_memory.avoid_list()}\n\n"
    user = (
        f"Channel niche: {cfg.get('niche')}\nAngle: {cfg.get('angle')}\n"
        f"Visual style: {cfg.get('style')}\nBANNED (never use): {cfg.get('banned')}\n"
        f"{topic_line}\n\n{fresh}{_format_note(t)}\n\n"
        f"Write ~{t['scene_count']} scenes ({t['scene_lo']}-{t['scene_hi']}). "
        f"Mark {t['hero_lo']}-{t['hero_hi']} scenes hero=true. scenes[0].narration must "
        "equal the hook.\n\n" + _schema_spec(t)
    )

    def _wc(d):
        return sum(len(str(s.get("narration", "")).split()) for s in d.get("scenes", []))

    def _expand(d: dict) -> dict:
        """Fatten an existing draft instead of demanding length up front.

        Small local models reliably write ~200 words no matter what you ask for,
        but they're good at 'say this same beat in more detail'. So we hand the
        draft back and ask for richer narration, same beats, same order."""
        scenes = d.get("scenes") or []
        skeleton = [{"narration": s.get("narration", ""), "hero": bool(s.get("hero"))}
                    for s in scenes]
        u = ("Below is a finished story, scene by scene. Keep the SAME scenes, the "
             "SAME order and the SAME plot — change nothing about what happens.\n"
             "Your ONLY job: rewrite each scene's narration so it is 25-35 spoken "
             "words (2-3 short sentences) instead of one line. Add concrete detail "
             "— what was said, what it looked like, what the narrator felt — but "
             "invent NO new events. Keep first person. Keep the final question.\n\n"
             f"Return the same JSON schema with all {len(scenes)} scenes.\n\n"
             + json.dumps({"title": d.get("title", ""), "hook": d.get("hook", ""),
                           "scenes": skeleton}, ensure_ascii=False))
        out = extract_json(complete(system, u, model_info, json_mode=True,
                                    temperature=0.7, timeout=900))
        new = out.get("scenes") or []
        if len(new) < len(scenes) * 0.7:
            return d                                   # it mangled the structure
        merged = []
        for i, s in enumerate(scenes):
            narr = str(new[i].get("narration", "")) if i < len(new) else ""
            merged.append({
                "narration": narr if len(narr.split()) > len(str(s.get("narration", "")).split()) else s.get("narration", ""),
                "image_prompt": s.get("image_prompt", ""),   # keep original visuals
                "hero": bool(s.get("hero")),
            })
        return {**d, "scenes": merged}

    # ---- CHUNKED CONTINUATION -------------------------------------------- #
    # A small local model reliably writes ~200 words no matter what you ask for.
    # So don't ask for 400 at once: write the story in batches of a few scenes,
    # feeding the story-so-far back each round, until the word target is met.
    # Each call is a small, easy task, which is why this actually holds up.
    CHUNK = 5

    def _chunk(prior: list, need: int, closing: bool) -> dict:
        if not prior:
            u = (user + f"\n\nWrite ONLY the FIRST {need} scenes now, plus title, "
                        "hook, description and tags. The story will continue after.")
        else:
            so_far = "\n".join(f"{i+1}. {s.get('narration','')}"
                               for i, s in enumerate(prior))
            u = (user + f"\n\nSTORY SO FAR (scenes 1-{len(prior)}):\n{so_far}\n\n"
                 f"Continue this EXACT story with the NEXT {need} scenes only. "
                 "Never repeat, restate or restart earlier scenes. "
                 + ("THIS IS THE ENDING: land the twist, resolve it, and make the "
                    "very last scene end on a question viewers will argue about."
                    if closing else
                    "Escalate — every scene adds a new reveal, reversal or consequence.")
                 + '\nReturn JSON: {"scenes":[{"narration":"25-35 words",'
                   '"image_prompt":"...","hero":true|false}]}')
        return extract_json(complete(system, u, model_info, json_mode=True,
                                     temperature=0.85, timeout=600))

    data: dict = {}
    scenes: list = []
    for _ in range(5):                                   # safety bound on rounds
        wc_now = sum(len(str(s.get("narration", "")).split()) for s in scenes)
        if scenes and (wc_now >= t["min_words"] or len(scenes) >= t["scene_hi"]):
            break
        need = min(CHUNK, max(1, t["scene_hi"] - len(scenes)))
        closing = (len(scenes) + need >= t["scene_hi"]
                   or wc_now + need * 28 >= t["approx_words"])
        try:
            part = _chunk(scenes, need, closing)
        except Exception as exc:  # noqa: BLE001
            log("warn", f"chunk failed ({exc})")
            break
        if not data:
            data = {k: part.get(k) for k in ("title", "hook", "description", "tags")}
        got = [s for s in (part.get("scenes") or []) if str(s.get("narration", "")).strip()]
        if not got:
            break
        scenes.extend(got)
        log("info", f"chunk +{len(got)} scenes (total {len(scenes)}, "
                    f"{sum(len(str(s.get('narration','')).split()) for s in scenes)} words)")
    if not scenes:                                       # chunking failed entirely
        data = extract_json(complete(system, user, model_info, json_mode=True,
                                     temperature=0.85, timeout=900))
    else:
        data = {**data, "scenes": scenes}
        if not data.get("hook"):
            data["hook"] = scenes[0].get("narration", "")
        data["scenes"][0]["narration"] = data["hook"]    # scene 1 must equal hook
    # ENFORCE the doctrine + length + originality: 2 rewrites naming exact faults.
    for attempt in range(1):
        faults = [] if lore else violations(data)
        dup = story_memory.duplicate_of(data)
        if dup:
            faults.append(f"repeats an earlier video ('{dup}') — invent a"
                          " completely different premise, cast and setting")
        words = _wc(data)
        if words < t["min_words"]:
            faults.append(f"only {words} spoken words — need AT LEAST "
                          f"{t['min_words']} (more scenes, 2-3 full sentences each; "
                          "real detail, not padding)")
        if not faults:
            break
        log("warn", f"script rejected ({attempt+1}): {'; '.join(faults)[:150]}")
        fix = (user + "\n\nYOUR LAST DRAFT WAS REJECTED. Faults:\n- "
               + "\n- ".join(faults) +
               "\n\nRewrite completely, obeying every rule: modern real-life drama, "
               "first person, conflict in sentence one, no fantasy whatsoever.")
        try:
            cand = extract_json(complete(system, fix, model_info, json_mode=True,
                                         temperature=0.9, timeout=480))
            cand_faults = [] if lore else violations(cand)
            if len(cand_faults) < len(faults) or _wc(cand) > words:
                data = cand
        except Exception as exc:  # noqa: BLE001
            log("warn", f"rewrite failed ({exc}); keeping current draft")
            break

    # Length is won by EXPANDING the draft, not by asking for more up front.
    for _ in range(2):
        have = _wc(data)
        if have >= t["min_words"]:
            break
        log("warn", f"expanding narration: {have} -> target {t['approx_words']} words")
        try:
            grown = _expand(data)
            if _wc(grown) <= have:
                break                                  # no progress, stop burning time
            data = grown
        except Exception as exc:  # noqa: BLE001
            log("warn", f"expansion failed ({exc}); keeping draft")
            break
    log("info", f"script final length: {_wc(data)} words "
                f"(~{round(_wc(data) / t['wpm'] * 60)}s at {t['wpm']} wpm)")
    return data


# --------------------------------------------------------------------------- #
# 3) Mock canned scripts (deterministic; the never-fail floor)
# --------------------------------------------------------------------------- #
def _scenes(rows):
    return [{"narration": n, "image_prompt": p, "hero": h} for (n, p, h) in rows]


def _history_short() -> dict:
    s = "vertical 9:16 composition, cinematic, moody, historical, volumetric light, film grain"
    rows = [
        ("Five thousand elite Roman soldiers marched north into the fog — and history "
         "never records them again.",
         f"A column of Roman legionaries vanishing into thick grey highland fog, eagle "
         f"standard raised, cold light, {s}", True),
        ("The Ninth Legion was legendary, hardened across the empire. Then the records "
         "that tracked every legion simply stop.",
         f"A weathered stone frontier fort at dawn, banners snapping in cold wind, {s}", True),
        ("Historians still argue about what happened. The honest answer is we don't "
         "actually know — and that's what makes it chilling.",
         f"Candlelit study piled with ancient maps and scrolls, a single quill, warm amber "
         f"glow, {s}", False),
        ("One theory: they were annihilated by the fierce tribes of the northern "
         "highlands, swallowed by a land Rome never tamed.",
         f"Shadowy tribal warriors watching from a fog-covered ridge above a narrow glen, {s}",
         True),
        ("Another: they were quietly transferred east, their paperwork lost to fire and "
         "time fifteen centuries ago.",
         f"A sunbaked eastern desert fortress under a vast sky, heat shimmer, distant "
         f"figures on the walls, {s}", True),
        ("An entire legion, erased from the best-documented empire in history. So what "
         "else have we simply lost?",
         f"Ghostly silhouettes of marching legionaries fading into white fog, dreamlike, {s}",
         True),
    ]
    return {
        "title": "The Roman Legion That Vanished Without a Trace",
        "hook": rows[0][0], "scenes": _scenes(rows),
        "description": ("5,000 elite Roman soldiers marched north — and the records just "
                        "stop. The still-unsolved mystery of the Ninth Legion. #shorts"),
        "tags": ["history", "shorts", "ancient rome", "roman empire", "ninth legion",
                 "unsolved mystery", "history shorts", "ancient history", "roman britain",
                 "mystery"],
    }


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


def _history_long() -> dict:
    style = "cinematic, moody, historical, volumetric light, film grain"
    rows = [
        ("In the year 108 AD, five thousand elite Roman soldiers marched north into the "
         "mist of Britain — and then, according to the records we have, they simply stopped "
         "existing. What happened to the Ninth Legion?",
         f"A long column of Roman legionaries marching into thick grey highland fog, eagle "
         f"standard raised, cold morning light, {style}", True),
        ("The Ninth Hispana was one of the most storied legions of the empire, hardened "
         "across a dozen frontiers. Then the imperial paperwork that tracked every legion "
         "goes quiet.",
         f"Weathered stone fort on a windswept northern frontier at dawn, banners snapping, "
         f"{style}", True),
        ("Historians have argued about this for more than a century, and the honest answer "
         "is that we still do not know. That uncertainty is exactly what makes it haunting.",
         f"Candlelit study filled with ancient maps and scrolls, a single quill resting, warm "
         f"amber glow, {style}", False),
        ("One theory is dramatic: that the legion marched into what is now Scotland and was "
         "annihilated by the fierce Caledonian tribes, swallowed by a land Rome never tamed.",
         f"Shadowy tribal warriors watching from a fog-covered ridge above a narrow glen, "
         f"tense and still, {style}", True),
        ("But the evidence is thin. A few roof tiles stamped with the legion's mark, a "
         "fragment of an inscription — clues that raise more questions than they answer.",
         f"Close-up of a broken clay tile with a faint stamped Roman numeral, half-buried in "
         f"dark soil, {style}", True),
        ("A quieter theory says there was no massacre at all. The legion may simply have "
         "been transferred east, its records lost the way so many ancient documents were "
         "lost — to fire, to time, to neglect.",
         f"Sunbaked eastern desert fortress under a vast sky, distant figures on the walls, "
         f"heat shimmer, {style}", True),
        ("Both stories cannot be true, and we may never have the document that settles it. "
         "The archive we would need to check might have burned fifteen centuries ago.",
         f"A great ancient library consumed by silent flames, embers rising into darkness, "
         f"{style}", True),
        ("What we can say is this: somewhere between the frontier of Britain and the edge of "
         "the known world, an entire legion slips out of history's grip.",
         f"A worn Roman road vanishing into cold mist between dark hills, empty and silent, "
         f"{style}", False),
        ("Maybe the truth is buried under a farmer's field, waiting for one more spade to "
         "turn it over. Archaeology has rewritten this story before, and it could again.",
         f"A single archaeologist's trowel resting beside a half-exposed bronze artifact in "
         f"rich earth, soft daylight, {style}", False),
        ("Until then, the Ninth Legion stays where it has been for nineteen centuries — not "
         "quite found, not quite lost, marching just beyond the edge of what we can prove.",
         f"Ghostly silhouettes of marching legionaries fading into white fog, dreamlike, "
         f"{style}", True),
        ("If a mystery this large can hide inside the best-documented empire of the ancient "
         "world, it makes you wonder how much of history is simply the part that survived.",
         f"A vast night sky over ancient ruins, stars wheeling above broken columns, "
         f"contemplative, {style}", False),
    ]
    return {
        "title": "The Roman Legion That Vanished Without a Trace",
        "hook": rows[0][0], "scenes": _scenes(rows),
        "description": ("Five thousand elite Roman soldiers marched north — and the records "
                        "simply stop. The honest, still-unsolved story of the Ninth Legion."),
        "tags": ["history", "ancient rome", "roman empire", "ninth legion", "unsolved mystery",
                 "documentary", "ancient history", "lost legion", "roman britain", "mystery"],
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
