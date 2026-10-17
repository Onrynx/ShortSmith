"""Continuity repair — contradictions, and people who were never invited.

Nothing else in the pipeline ever re-reads the story as a whole. The planner
sees beats, and the writer sees one chunk at a time plus a list of lines already
written. So a model will happily open with "another letter addressed to Sarah"
and, six scenes later, write "she had been sending them to me" — each line fine
on its own, the pair impossible. It will also introduce a "Mike" who was never
in the cast, because at the moment of writing that scene, nothing says he isn't.

This module is the only stage that sees the finished draft at once:

    unknown_names()   finds cast strangers in Python — exact, free, and it lets
                      the editor be told precisely who to fix rather than asked
                      to go hunting
    continuity_pass() one model call that rewrites ONLY the broken lines

Both are advisory: if the call fails, the draft passes through untouched. A
continuity wobble is worth fixing, never worth losing a render over.
"""
from __future__ import annotations

import difflib
import re

from .llm import complete
from .utils import log

# Capitalised words that begin sentences or are simply common. Without this the
# name scan flags "Today", "Inside" and "Because" on nearly every run.
_NOT_NAMES = {
    "The", "This", "That", "There", "Then", "They", "Their", "Them", "These",
    "Those", "What", "When", "Where", "While", "Why", "How", "Who", "But",
    "And", "After", "Before", "Inside", "Outside", "Today", "Tonight",
    "Yesterday", "Tomorrow", "Later", "Now", "Last", "Next", "Every", "Each",
    "Some", "Something", "Someone", "Nothing", "Nobody", "She", "Her", "Hers",
    "His", "Him", "Our", "Ours", "Your", "Yours", "Its", "Not", "For", "From",
    "With", "Without", "Because", "Maybe", "Even", "Only", "Just", "Still",
    "Once", "Until", "Since", "Been", "Have", "Here", "Over", "Into", "About",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "June", "July",
    "August", "September", "October", "November", "December",
    "God", "Mom", "Dad", "Mum", "Okay", "Yes", "Sure", "Well",
    "Another", "Anyway", "Eventually", "Suddenly", "Finally",
}
_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")


def unknown_names(script: dict, plan: dict) -> dict:
    """Named people in the prose who were never in the plan -> {name: scene_no}."""
    known = " ".join([
        " ".join(str(c.get("name", "")) for c in (plan.get("cast") or [])
                 if isinstance(c, dict)),
        str(plan.get("premise", "")),
        str(plan.get("logline", "")),
        str(plan.get("setting", "")),
        str(plan.get("narrator", "")),
        str(plan.get("title_idea", "")),
    ]).lower()
    # A capitalised word only counts as a name if it ever appears MID-sentence.
    # Sentence-initial capitals are the whole false-positive problem: "Another
    # letter arrived" was being reported as a character called Another, which
    # then triggered a needless (and slow) model call. A real name almost
    # always shows up somewhere other than after a full stop.
    found: dict = {}
    midsentence: set = set()
    appearances: dict = {}
    for sc in script.get("scenes") or []:
        text = str(sc.get("narration", ""))
        for m in _NAME_RE.finditer(text):
            word = m.group(1)
            appearances.setdefault(word, set()).add(id(sc))
            before = text[:m.start()].rstrip()
            after = text[m.end():m.end() + 2]
            # Mid-sentence is the strongest signal, but a name can legitimately
            # open a sentence. Two cheaper tells rescue those: an appositive
            # ("Cindy, my neighbour") or a possessive ("Cindy's car").
            if before and not before.endswith((".", "!", "?")):
                midsentence.add(word)
            elif after.startswith(",") or after.startswith("'s"):
                midsentence.add(word)
    # A proper noun used across several scenes is a character, not an accident.
    for word, scenes_seen in appearances.items():
        if len(scenes_seen) >= 2:
            midsentence.add(word)
    for i, sc in enumerate(script.get("scenes") or []):
        for m in _NAME_RE.finditer(str(sc.get("narration", ""))):
            name = m.group(1)
            if name in _NOT_NAMES or name.lower() in known:
                continue
            if name not in midsentence:
                continue
            found.setdefault(name, i + 1)
    return found


def duplicate_scenes(script: dict, threshold: float = 0.58) -> list:
    """Pairs of scenes saying nearly the same thing -> [(i, j, ratio), ...].

    The writer sometimes spends two beats on one event — a live run produced
    "Tom came over, nosy as ever..." immediately followed by "Tom came over,
    asking questions..." — which burns runtime and makes the story feel stuck.
    Similarity is measured here rather than asked of the model, because a
    ratio is exact and a model's opinion of "similar" is not.
    """
    scenes = script.get("scenes") or []
    norm = []
    for sc in scenes:
        words = re.findall(r"[a-z']+", str(sc.get("narration", "")).lower())
        norm.append(" ".join(words))
    out = []
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            if not norm[i] or not norm[j]:
                continue
            r = difflib.SequenceMatcher(None, norm[i], norm[j]).ratio()
            if r >= threshold:
                out.append((i + 1, j + 1, round(r, 2)))
    return out


_SYS = (
    "You are a continuity editor. You read a finished short story and fix only "
    "the lines that contradict an earlier line, or that introduce a person who "
    "does not belong. You never change the plot, never reorder scenes, never "
    "touch a line that is already consistent, and you keep each rewritten line "
    "about the same length as the original. You return strict JSON only."
)


def continuity_pass(script: dict, plan: dict, model_info: dict,
                    timeout: float = 600, always: bool = False) -> dict:
    """Fix contradictions and cast strangers. Returns the script either way.

    The model call is only made when the free detectors have actually found
    something. On a machine where one call costs 30-120 seconds, adding an
    unconditional pass to every render is a poor trade: strangers and
    duplicates are caught mechanically and exactly, while a pure contradiction
    hunt is the expensive, lower-yield case. Pass always=True to force it.
    """
    scenes = script.get("scenes") or []
    if len(scenes) < 2:
        return script

    strangers = unknown_names(script, plan)
    cast = ", ".join(f"{c.get('name')} ({c.get('role')})"
                     for c in (plan.get("cast") or []) if isinstance(c, dict))

    stranger_note = ""
    if strangers:
        who = ", ".join(f"{n} (first in scene {i})"
                        for n, i in list(strangers.items())[:8])
        stranger_note = (
            "\n\nThese names appear but were never part of the cast: " + who +
            ".\nReplace each with someone from the cast, or make the reference "
            'anonymous ("a neighbour", "a friend"). If one of them is really a '
            "place or an ordinary word, leave it alone."
        )

    dup_note = ""
    dups = duplicate_scenes(script)
    if dups:
        pairs = "; ".join(f"scenes {i} and {j} ({int(r * 100)}% the same)"
                          for i, j, r in dups[:5])
        jobs = ", ".join(
            f"scene {j} should be the '{scenes[j - 1].get('purpose', 'next')}' beat"
            for _i, j, _r in dups[:5])
        dup_note = ("""

These scenes repeat each other: """ + pairs + """.
Rewrite the LATER scene of each pair so it moves the story somewhere new
instead of restating the earlier one: """ + jobs + """.
Leave the earlier scene of each pair exactly as it is.""")

    if not strangers and not dups and not always:
        log("info", "continuity: nothing flagged, skipping the model call")
        return script

    numbered = "\n".join(f"{i + 1}. {s.get('narration', '')}"
                         for i, s in enumerate(scenes))

    user = (
        f"PREMISE: {plan.get('premise', '')}\n"
        f"NARRATOR: {plan.get('narrator', '')}\n"
        f"CAST (the only people who exist): {cast or 'the narrator only'}\n\n"
        f"THE STORY:\n{numbered}\n\n"
        "Find every line that contradicts an earlier one — a fact stated two "
        "different ways, the direction of events reversed, someone doing "
        "something impossible given what we were already told. Fix ONLY those "
        "lines." + stranger_note + dup_note + "\n\n"
        'Return ONLY the lines you changed, as {"scenes":[{"index":0,'
        '"narration":"..."}]}\n'
        'If nothing is wrong, return {"scenes":[]}.'
    )

    try:
        from .crews.story_crew import extract_json
        out = extract_json(complete(_SYS, user, model_info, json_mode=True,
                                    temperature=0.4, timeout=timeout))
    except Exception as exc:  # noqa: BLE001
        log("warn", f"continuity pass failed ({exc}); keeping draft")
        return script

    fixed = 0
    changed: list = []
    for item in out.get("scenes") or []:
        try:
            i = int(item.get("index"))
            new = str(item.get("narration", "")).strip()
        except Exception:  # noqa: BLE001
            continue
        if not new or not (0 <= i < len(scenes)):
            continue
        if new == scenes[i].get("narration"):
            continue
        # A repair that turns this scene into a near-copy of another is worse
        # than the problem it fixed — a live run "repaired" scene 5 into a
        # restatement of scene 4. Reject those rather than apply them.
        norm_new = " ".join(re.findall(r"[a-z']+", new.lower()))
        clash = False
        for k, other in enumerate(scenes):
            if k == i:
                continue
            norm_other = " ".join(re.findall(r"[a-z']+",
                                             str(other.get("narration", "")).lower()))
            if norm_new and norm_other and difflib.SequenceMatcher(
                    None, norm_new, norm_other).ratio() >= 0.58:
                clash = True
                break
        if clash:
            log("warn", f"continuity: rejected repair of scene {i + 1} "
                        "(would duplicate another scene)")
            continue
        scenes[i]["narration"] = new
        changed.append(i + 1)
        fixed += 1

    if fixed:
        script["scenes"] = scenes
        script["hook"] = scenes[0].get("narration", script.get("hook", ""))
    log("info", f"continuity: {len(strangers)} stranger(s), {len(dups)} "
                f"duplicate pair(s) flagged; repaired {changed or 'nothing'}")
    return script
