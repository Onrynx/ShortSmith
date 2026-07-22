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
    "a hospital waiting room", "a car dealership", "a wedding rehearsal dinner",
    "a construction site", "a school parents evening", "a storage unit auction",
    "a golf trip", "a family group chat", "a bank branch", "a police station",
    "a gym locker room", "a food delivery shift", "a poker night",
    "a hotel lobby at 2am", "a shared driveway", "a will reading",
]
_PEOPLE = [
    "my sister", "my best friend", "my roommate", "my boss", "a coworker",
    "my stepmom", "my neighbour", "my landlord", "my ex", "my cousin",
    "my mother-in-law", "my brother", "my dad", "a teacher", "a customer",
    "my fiancee's family", "a stranger who knew my name", "my childhood friend",
    "my wife", "my girlfriend", "my stepdad", "my uncle", "my business partner",
    "my old college roommate", "my son's teacher", "my brother-in-law",
    "my grandmother", "my new manager", "a groomsman", "my downstairs tenant",
    "my sister's fiance", "my estranged twin", "my father's lawyer",
]
_ENGINES = [
    "an inheritance nobody was supposed to know about", "a cheating rumour",
    "a hidden camera", "a DNA test result", "stolen identity",
    "a false accusation of theft", "a text sent to the wrong person",
    "a secret second job", "money that vanished from a shared account",
    "blatant favouritism", "a scam that almost worked", "a leaked group chat",
    "a prank that went too far", "a lie told years ago", "a secret recording",
    "someone pretending to be broke", "a fake emergency",
    "a forged signature", "an inheritance quietly rewritten", "a paternity secret",
    "a business partner skimming money", "a fake injury claim",
    "a deposit that never got paid", "a rigged family vote", "a stolen promotion",
    "an anonymous complaint to HR", "a doorbell camera recording",
    "a debt someone hid for years", "a tenant who was never on the lease",
]
_TWISTS = [
    "the 'villain' was actually protecting someone",
    "the accusation was true, but for a completely different reason",
    "the money had belonged to the narrator all along",
    "the prank became the evidence that saved them",
    "the anonymous messages were coming from a parent",
    "the person everyone defended had staged the whole thing",
    "the narrator had quietly set the trap weeks earlier",
    "the one person everyone blamed had been covering for someone else",
    "the evidence proved the opposite of what everyone assumed",
    "the narrator was the only one who did not know the truth",
    "the 'mistake' had been deliberate from the very first day",
    "the person who exposed it had the most to lose",
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
        f"STORY SEED #{random.randint(1000, 9999)} for THIS video (your starting "
        "point — build something original from it, never copy the wording): "
        f"the narrator is a MAN in conflict with {random.choice(_PEOPLE)} "
        f"at {random.choice(_SETTINGS)}, driven by {random.choice(_ENGINES)}, "
        f"and the twist is that {random.choice(_TWISTS)}."
    )


_COMMON = {"about", "after", "with", "that", "this", "they", "them", "then", "when",
           "what", "were", "into", "from", "your", "just", "know", "told", "said",
           "over", "back", "took", "made", "went", "came", "would", "could", "there",
           "their", "story", "shorts", "short", "video"}


def _elements(row: dict) -> set[str]:
    """Distinctive words that define a story (people, objects, situations)."""
    return (_norm(row.get("title", "")) | _norm(row.get("hook", ""))) - _COMMON


def duplicate_of(script: dict) -> str | None:
    """Return the clashing past title if this story echoes a recent one.

    Deliberately strict — near-misses are what make a channel feel repetitive."""
    cur = _elements(script)
    if not cur:
        return None
    for row in recent():
        prev = _elements(row)
        if not prev:
            continue
        overlap = len(cur & prev) / max(1, len(cur | prev))
        if overlap > 0.3 or len(cur & prev) >= 4:
            return row.get("title", "")
    return None


def avoid_list(limit: int = 25) -> str:
    """Everything already used — titles, openings, and the raw elements — so the
    model cannot drift back into a premise it has written before."""
    rows = recent(limit)
    if not rows:
        return ""
    titles = "; ".join(f'"{r.get("title","")}"' for r in rows if r.get("title"))
    used: set[str] = set()
    for r in rows:
        used |= _elements(r)
    parts = ["ALREADY USED — this channel has ALREADY published these. Your story "
             "must share NO premise, character role, object or setup with any of them:"]
    if titles:
        parts.append("Titles: " + titles)
    if used:
        parts.append("Do NOT build the plot around any of these elements again: "
                     + ", ".join(sorted(used)[:60]))
    parts.append("Pick a DIFFERENT relationship, a DIFFERENT place and a DIFFERENT "
                 "kind of betrayal than every one of the above.")
    return "\n".join(parts)
