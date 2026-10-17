"""The planning stage — turn a vague human brief into a developed story concept.

This is the layer StoryForge was missing. Before this existed, a request went
straight from "make a story about loneliness" into a scene-by-scene writer that
had been handed a fixed arc (hook -> setup -> escalation -> twist -> ending) and
a flat word budget. Every video therefore had the same shape and the same
breathless pacing, whatever the idea was.

The planner does three jobs the writer cannot do while it is busy writing prose:

1. INTERPRET. Read what the user actually meant. A brief can be a subject
   ("a guy who finds something strange in his house"), a theme ("revolve it
   around loneliness"), a constraint ("a specific historical event"), an exact
   opening line, or nothing at all. It infers the register the idea belongs to
   instead of forcing every idea into the same genre.

2. DEVELOP. Invent the premise, the cast, the setting, the specific concrete
   detail, and — crucially — make the user's theme STRUCTURAL. If they asked for
   loneliness, loneliness has to drive what happens, not get mentioned twice.

3. SHAPE + PACE. Choose the structure that suits THIS story from a menu of real
   shapes (or invent one), then lay out beats where each beat states its own
   job and how many seconds it deserves. Setup beats get room to establish who
   and where before anything escalates; that is the fix for the "everything
   happens instantly" problem, and it costs no extra runtime because the time is
   taken from beats that were rushing anyway.

The plan is data the rest of the pipeline reads: the writer writes to the beats,
the timing module budgets words per beat, and the visual stage gets a real
description of what is on screen instead of an unrelated stock prompt.
"""
from __future__ import annotations

import json
import re

from .llm import complete
from .timing import words_for_seconds
from .utils import log

# Real structural shapes. The model PICKS one (or writes its own) — the point is
# that a story is no longer forced through a single arc. Each shape implies a
# different opening, a different place for context, and a different ending.
STRUCTURES = """AVAILABLE STORY SHAPES (choose the one that fits THIS idea, or invent a better one):
- cold_open_flashback: open mid-crisis, jump back to explain how we got here, catch up, then push past it.
- slow_dread: quiet, normal, specific — small wrongness accumulates until it can't be explained away.
- escalating_confrontation: a conflict that keeps raising the stakes as more people are dragged in.
- discovery: someone finds a thing; the story is the widening understanding of what it means.
- two_timelines: cut between then and now so the past explains the present at the right moment.
- reframe: tell it one way, then reveal the fact that makes every earlier scene mean something else.
- character_study: one person under pressure; the events matter because of what they reveal about them.
- confession: the narrator admits something, circling closer to the part they don't want to say.
- chronicle: an event told in order because the sequence itself is the payload (works for history).
- quiet_ending: everything resolves except the feeling — the last beat sits in the aftermath."""

# Beat purposes the writer and the timing module both understand.
PURPOSES = ("establish", "context", "develop", "complicate", "turn",
            "reveal", "escalate", "resolve", "aftermath")

_SYS = (
    "You are a story development editor for short-form video. You take a rough "
    "idea from a creator and develop it into a concrete, well-shaped story plan. "
    "You think about what the audience needs to understand and FEEL at each "
    "moment, and you are ruthless about specificity: real names, real places, "
    "real details, never vague abstractions. You return strict JSON only."
)


def _extract_json(text: str) -> dict:
    if not text:
        raise ValueError("empty plan output")
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in plan")
    depth, in_str, esc = 0, False, False
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
    raise ValueError("unbalanced JSON in plan")


def classify_brief(brief: str) -> dict:
    """Cheap, local read of WHAT KIND of instruction the user gave.

    Done in Python because it is deterministic and costs no model call. The plan
    prompt uses this to frame the request correctly — a theme is not a subject,
    and an exact opening line must survive verbatim.
    """
    b = (brief or "").strip()
    if not b:
        return {"kind": "open", "value": ""}
    if re.search(r"\b(revolve|centre|center|about the (theme|idea)|theme of|"
                 r"explore|feel(ing)? of|capture)\b", b, re.I):
        return {"kind": "theme", "value": b}
    if re.search(r"\b(historical|history|real event|actually happened|true story|"
                 r"in \d{3,4}|war|dynasty|revolution)\b", b, re.I):
        return {"kind": "historical", "value": b}
    # an exact line to open on: first-person sentence of reasonable length
    if re.match(r"^(my|i|he|she|they|we|our)\b", b, re.I) and len(b.split()) >= 4:
        return {"kind": "opening_line", "value": b}
    return {"kind": "subject", "value": b}


def _brief_instruction(brief: str) -> str:
    info = classify_brief(brief)
    kind, val = info["kind"], info["value"]
    if kind == "open":
        return ("THE CREATOR GAVE NO BRIEF. Invent an original, specific premise "
                "you find genuinely interesting.")
    if kind == "theme":
        return (f'THE CREATOR GAVE A THEME: "{val}"\n'
                "This theme must be STRUCTURAL, not decorative. The premise, the "
                "central choice, the turn and the ending must all be expressions "
                "of this theme. A viewer should feel it without it ever being "
                "named aloud. Do NOT simply mention the word a few times.")
    if kind == "historical":
        return (f'THE CREATOR ASKED FOR A REAL/HISTORICAL SUBJECT: "{val}"\n'
                "Pick ONE specific, real, well-documented event or moment. Use "
                "real names, dates and places. Be honest where the record is "
                "uncertain — say so rather than inventing certainty.")
    if kind == "opening_line":
        return (f'THE CREATOR GAVE THE EXACT OPENING LINE: "{val}"\n'
                "This sentence must be the first thing spoken, word for word. "
                "Build the entire story so that this line is the true starting "
                "point — everything that follows must earn and explain it.")
    return (f'THE CREATOR GAVE A SUBJECT: "{val}"\n'
            "Develop this into a specific story. Decide who the person is, where "
            "they are, what is actually going on, and why it matters. Do not ask "
            "the creator for more detail — make confident choices yourself.")


def _fallback_plan(brief: str, cfg: dict, target_seconds: float) -> dict:
    """A usable plan when the model can't be reached — never blocks a render."""
    n = 8
    per = max(6.0, target_seconds / n)
    purposes = ["establish", "context", "develop", "complicate", "turn",
                "escalate", "reveal", "resolve"]
    return {
        "premise": brief or (cfg.get("angle") or "an ordinary situation that goes wrong"),
        "logline": brief or "Something ordinary turns out to be anything but.",
        "setting": "a specific, ordinary modern place",
        "narrator": "first person, male",
        "theme": brief if classify_brief(brief)["kind"] == "theme" else "",
        "structure": "slow_dread",
        "why_structure": "fallback plan",
        "tone": cfg.get("style", "grounded and tense"),
        "ending_kind": "unsettled",
        "beats": [{"purpose": p, "what_happens": "", "why_it_matters": "",
                   "seconds": round(per, 1), "visual": ""} for p in purposes],
        "_fallback": True,
    }


def _normalize_plan(plan: dict, target_seconds: float, scene_bounds: tuple,
                    wpm: float, calibration: float) -> dict:
    """Repair a model plan: sane beat count, purposes, and seconds that ADD UP."""
    lo, hi = scene_bounds
    beats = [b for b in (plan.get("beats") or []) if isinstance(b, dict)]
    # keep beats that actually say something
    beats = [b for b in beats if str(b.get("what_happens", "")).strip()] or beats
    if not beats:
        beats = _fallback_plan("", {}, target_seconds)["beats"]
    if len(beats) > hi:
        beats = beats[:hi]
    while len(beats) < lo:
        # duplicate a development beat rather than inventing structure
        beats.append({**beats[min(len(beats) - 1, 2)], "purpose": "develop",
                      "what_happens": "", "seconds": 0})

    for b in beats:
        p = str(b.get("purpose", "")).lower().strip()
        b["purpose"] = p if p in PURPOSES else "develop"
        try:
            b["seconds"] = float(b.get("seconds") or 0)
        except Exception:
            b["seconds"] = 0.0

    # Terminal beats must actually be terminal. A real plan came back with a
    # 'develop' beat AFTER 'aftermath', so the finished story carried on past
    # its own ending. Reordering (rather than relabelling) is the right repair:
    # the content of a resolve/aftermath beat genuinely belongs at the end, it
    # was just placed wrongly. A stable partition keeps everything else in the
    # order the planner intended.
    TERMINAL = ("resolve", "aftermath")
    if any(b["purpose"] in TERMINAL for b in beats):
        head = [b for b in beats if b["purpose"] not in TERMINAL]
        tail = [b for b in beats if b["purpose"] in TERMINAL]
        if head:                      # never let the reorder empty the body
            beats = head + tail

    # Rescale the model's allocation so the beats sum to the real target. This
    # keeps the SHAPE it chose (which beat gets more room) while making the
    # arithmetic correct.
    total = sum(b["seconds"] for b in beats)
    if total <= 0:
        for b in beats:
            b["seconds"] = target_seconds / len(beats)
    else:
        scale = target_seconds / total
        for b in beats:
            b["seconds"] = b["seconds"] * scale

    # Floor every beat, then re-normalise so the floors don't inflate the total.
    for b in beats:
        b["seconds"] = max(3.0, b["seconds"])
    # Hard cap the opening: a live run produced a 12-second beat of someone
    # describing their daily routine, which is description, not a hook. Time
    # taken off the opening goes back to the beats that actually need it.
    if beats and beats[0]["seconds"] > 8.0:
        beats[0]["seconds"] = 8.0
    total = sum(b["seconds"] for b in beats)
    scale = target_seconds / total if total > 0 else 1.0
    for b in beats:
        b["seconds"] = round(max(2.6, b["seconds"] * scale), 1)
        b["words"] = words_for_seconds(b["seconds"], wpm, calibration, scene_count=1)

    plan["beats"] = beats
    plan["planned_seconds"] = round(sum(b["seconds"] for b in beats), 1)
    plan["planned_words"] = sum(b["words"] for b in beats)
    return plan


def build_plan(brief: str, cfg: dict, model_info: dict, *, target_seconds: float,
               scene_bounds: tuple, wpm: float, calibration: float = 1.0,
               avoid: str = "", seed: str = "", narrator_gender: str = "male") -> dict:
    """Develop a brief into a full story plan. One model call. Never raises."""
    lo, hi = scene_bounds
    mode = cfg.get("story_mode", "real_life_drama")
    guide = _brief_instruction(brief)
    kind = classify_brief(brief)["kind"]

    # Genre guidance is a DEFAULT, not a cage: an explicit historical or other
    # request from the user overrides the channel's usual register.
    if kind == "historical":
        register = ("REGISTER: real history, told like a documentary narrator who "
                    "knows how to hold an audience. Real names and dates.")
    elif mode == "lore":
        register = ("REGISTER: cinematic game-lore storytelling. Worldbuilding is "
                    "welcome here.")
    else:
        register = ("REGISTER: modern, grounded, real life. The kind of story a "
                    "person tells you at 2am and you believe them. No fantasy, no "
                    "magic, no worldbuilding — unless the creator's brief above "
                    "explicitly asks for something else, in which case follow the "
                    "creator.")

    freshness = f"\n{seed}\n{avoid}\n" if (seed or avoid) and kind in ("open",) else ""

    # The TTS voice is a fixed gender, so the person telling the story has to
    # match it or the finished video sounds wrong. This is a hardware constraint,
    # not a creative one — other characters can be anyone.
    voice_rule = (
        f"THE NARRATOR IS {narrator_gender.upper()}. The video is read aloud by a "
        f"{narrator_gender} voice, so whoever tells this story must be {narrator_gender}. "
        "Other characters can be any gender. Do not make the narrator "
        f"{'female' if narrator_gender == 'male' else 'male'}."
    )

    user = f"""{guide}

{register}
{voice_rule}
Channel niche: {cfg.get('niche')}
Visual style available: {cfg.get('style')}
NEVER include: {cfg.get('banned')}
{freshness}
TARGET LENGTH: about {int(target_seconds)} seconds of finished video, told in
{lo}-{hi} beats. You decide how to spend that time.

{STRUCTURES}

HOW TO PACE IT (this matters more than anything else):
The most common failure in short video is racing. A story that introduces
something and immediately escalates gives the viewer nothing to care about, so
the escalation lands on no one. Before the first real turn, the audience must
know: who this person is, where they are, what normal looks like, and what is
at stake for them. Spend real seconds on that — it is not wasted time, it is
what makes the rest work.
But do NOT pad. Establish with specific, concrete detail (a name, an object, a
habit, one line of dialogue), not with general description. When a beat's job is
done, move.

THE OPENING IS A SPECIAL CASE. Establishing does not mean starting boring. The
first beat has to do BOTH jobs at once: ground the viewer AND give them a reason
to stay. A plain description of someone's daily routine is not a reason to stay.
Something must be slightly off, unresolved, or unusually specific — a detail
that raises a question the viewer wants answered. You do not have to state the
conflict in the first line (that is a cliché of its own), but nothing may be
inert. Keep the opening beat SHORT — roughly 4 to 8 seconds. If it is longer
than that, it is description, not a hook.

Decide for yourself:
- how long the opening should be before anything goes wrong
- whether this story even HAS a twist (many good stories do not)
- whether it ends on a question, an image, a consequence, or silence
Different stories should come out different shapes. Do not default to one.

EVERY BEAT MUST EARN ITS PLACE. No two beats may cover the same event. If two
beats could be described with the same sentence, they are one beat — merge them
and use the freed seconds elsewhere. Each beat has to leave the viewer knowing
or feeling something they did not a moment ago.

NAME YOUR CAST UP FRONT. Everyone who appears anywhere in the story must be in
the cast list below. A person who turns up in a later beat without having been
established reads as a mistake, because to the viewer it is one.

For EACH beat give:
  purpose      one of: {', '.join(PURPOSES)}
  what_happens the concrete events of this beat (specific, not abstract)
  why_it_matters what the viewer now understands or feels that they did not before
  seconds      how many seconds this beat deserves (they should sum to ~{int(target_seconds)})
  visual       what is literally ON SCREEN for this beat

Return ONLY strict JSON:
{{"premise":"...","logline":"one sentence","setting":"...","narrator":"who is telling it ({narrator_gender}, first person)",
 "cast":[{{"name":"...","role":"..."}}],"theme":"what it's really about",
 "structure":"chosen shape","why_structure":"why this shape suits this idea",
 "tone":"...","ending_kind":"...","title_idea":"...",
 "beats":[{{"purpose":"...","what_happens":"...","why_it_matters":"...","seconds":0,"visual":"..."}}]}}"""

    try:
        raw = complete(_SYS, user, model_info, json_mode=True, temperature=0.9,
                       timeout=600)
        plan = _extract_json(raw)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"planner failed ({exc}); using fallback plan")
        plan = _fallback_plan(brief, cfg, target_seconds)

    plan.setdefault("brief", brief)
    plan.setdefault("brief_kind", kind)
    plan = _normalize_plan(plan, target_seconds, scene_bounds, wpm, calibration)
    log("info", f"plan: '{str(plan.get('logline'))[:70]}' — structure="
                f"{plan.get('structure')} · {len(plan['beats'])} beats · "
                f"{plan['planned_seconds']}s planned")
    return plan


def plan_summary(plan: dict) -> str:
    """Compact context handed to the writer so nothing is lost between stages."""
    cast = ", ".join(f"{c.get('name')} ({c.get('role')})"
                     for c in (plan.get("cast") or []) if isinstance(c, dict))
    lines = [
        f"PREMISE: {plan.get('premise','')}",
        f"LOGLINE: {plan.get('logline','')}",
        f"SETTING: {plan.get('setting','')}",
        f"NARRATOR: {plan.get('narrator','')}",
    ]
    if cast:
        lines.append(f"CAST: {cast}")
    if plan.get("theme"):
        lines.append(f"THEME (must be structural): {plan['theme']}")
    lines.append(f"STRUCTURE: {plan.get('structure','')} — {plan.get('why_structure','')}")
    lines.append(f"TONE: {plan.get('tone','')}")
    lines.append(f"ENDING: {plan.get('ending_kind','')}")
    return "\n".join(lines)
