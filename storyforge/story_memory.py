"""Never tell the same story twice.

Remembers every story shipped, injects a randomized STORY SEED so the model can't
settle into one groove, and rejects a draft that repeats a recent title/hook.
"""
from __future__ import annotations

import random
import re

from .utils import STATE_DIR, read_json, utcnow_iso, write_json

MEM_FILE = STATE_DIR / "recent_stories.json"
KEEP = 60

_SETTINGS = [
    "a wedding", "a family dinner", "an office", "a college dorm", "a hospital",
    "an airport", "a road trip", "a group chat", "an apartment building",
    "a restaurant shift", "a high school", "a gym", "a funeral", "a house party",
    "a hotel", "a supermarket", "a parking lot", "a rideshare", "a beach vacation",
    "a company retreat", "a courtroom waiting room", "a moving day",
]
_PEOPLE = [
    "my sister", "my best friend", "my roommate", "my boss", "a coworker",
    "my stepmom", "my neighbour", "my landlord", "my ex", "my cousin",
    "my mother-in-law", "my brother", "my dad", "a teacher", "a customer",
    "my fiance's family", "a stranger who knew my name", "my childhood friend",
]
_ENGINES = [
    "an inheritance nobody was supposed to know about", "a cheating rumour",
    "a hidden camera", "a DNA test result", "stolen identity",
    "a false accusation of theft", "a text sent to the wrong person",
    "a secret second job", "money that vanished from a shared account",
    "blatant favouritism", "a scam that almost worked", "a leaked group chat",
    "a prank that went too far", "a lie told years ago", "a secret recording",
    "someone pretending to be broke", "a fake emergency",
]
_TWISTS = [
    "the 'villain' was actually protecting someone",
    "the accusation was true, but for a completely different reason",
    "the money had belonged to the narrator all along",
    "the prank became the evidence that saved them",
    "the anonymous messages were coming from a parent",
    "the person everyone defended had staged the whole thing",
    "the narrator had quietly set the trap weeks earlier",
]


def _norm(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if len(w) > 3}


def recent(n: int = KEEP) -> list[dict]:
    return (read_json(MEM_FILE, []) or [])[-n:]


def remember(title: str, hook: str) -> None:
    rows = read_json(MEM_FILE, []) or []
    rows.append({"title": title, "hook": hook, "ts": utcnow_iso()})
    write_json(MEM_FILE, rows[-KEEP:])


def seed_prompt() -> str:
    """A fresh random premise every run so stories never converge."""
    return (
        "STORY SEED for THIS video (your starting point — build something original "
        f"from it, do not copy the wording): a conflict with {random.choice(_PEOPLE)} "
        f"at {random.choice(_SETTINGS)}, driven by {random.choice(_ENGINES)}, "
        f"and the twist is that {random.choice(_TWISTS)}."
    )


def duplicate_of(script: dict) -> str | None:
    """Return the clashing past title if this story repeats a recent one."""
    t, h = _norm(script.get("title", "")), _norm(script.get("hook", ""))
    for row in recent():
        pt, ph = _norm(row.get("title", "")), _norm(row.get("hook", ""))
        for a, b in ((t, pt), (h, ph)):
            if a and b and len(a & b) / max(1, len(a | b)) > 0.5:
                return row.get("title", "")
    return None


def avoid_list(limit: int = 18) -> str:
    rows = recent(limit)
    if not rows:
        return ""
    titles = "; ".join(f'"{r.get("title","")}"' for r in rows if r.get("title"))
    return ("ALREADY USED — do not repeat these premises, characters or titles:\n"
            + titles) if titles else ""
