"""THE STORY DOCTRINE — the standing law for every script StoryForge writes.

Short-form vertical video: the gameplay is only background, the STORY is the
product. We are not writing literature. We are engineering attention.

This module is the single source of truth: the script writer (crews/story_crew.py)
injects DOCTRINE into every prompt, `violations()` mechanically ENFORCES it (a
script that breaks the hard rules is rejected and rewritten), and Valentine
(nl_agent.py) carries DOCTRINE_SUMMARY so the bot upholds the same standard.
"""
from __future__ import annotations

import re

DOCTRINE = """YOU ARE ENGINEERING ATTENTION, NOT WRITING LITERATURE.
Maximize: retention, emotional engagement, suspense, comments, rewatches.

CORE PRINCIPLE — every story must feel like "I genuinely think this could happen."
The viewer should constantly think: "What would I do?" / "Who's actually wrong
here?" / "No way that just happened." They must NEVER think "this sounds
AI-generated."

ABSOLUTELY FORBIDDEN: fantasy, magic, wizards, ancient kingdoms, dragons, gods,
superheroes, time travel, sci-fi, aliens, parallel universes, mythology, Zelda,
Minecraft lore, fictional artifacts/races, worldbuilding, medieval settings,
space operas, RPG-style stories, crowns/prophecies/realms, poetic writing,
long description, excessive narration. EVERYTHING happens in modern real life.

SETTINGS (places people know): school, college, dorm, family dinner, wedding,
restaurant, mall, airport, hotel, office, workplace, hospital, neighborhood,
apartment, gym, vacation, road trip, Uber, coffee shop, house party, gas station,
parking lot, concert, movie theater, group chats, DMs, text messages, social media.

TOPICS: family drama, relationship drama, cheating, lying, secrets, hidden
identities, inheritance, money, friend betrayal, parents, siblings, step-parents,
roommates, marriage, breakups, school scandals, teachers, bullies, coworkers,
bosses, customers, neighbors, landlords, police misunderstandings, unexpected
kindness, petty revenge, karma, public embarrassment, fake accusations, lost
phones, hidden cameras, surprise guests, adoption secrets, DNA tests, scams,
catfishing, identity theft, wedding/airport/travel disasters, entitled people,
favoritism, jealousy, pranks gone wrong, exposed lies, texting the wrong person.

NARRATION: FIRST PERSON, always. "I walked in..." never "John walked in...".
It should feel like hearing a confession.

OPENING: the FIRST sentence must contain the conflict — someone did something to
the narrator, stated flatly, in under 15 words. Shape: "My <person> <did the
damaging thing>." Invent your OWN; do not copy any example you have ever seen.
  BANNED openers: "My name is...", "When I was growing up...", "It was a sunny
  day...", "Imagine...", "What if I told you...".

RETENTION: something CHANGES every 2-3 sentences. Problem -> new complication ->
another reveal -> bigger problem -> consequence -> twist -> ending. The viewer
should never feel safe. Escalate: argument -> parents involved -> police involved
-> video evidence -> truth revealed -> confession -> final twist.

CHARACTERS: real people. They lie, panic, cry, hide things, ghost, overreact,
manipulate, apologize, get jealous and defensive. Nobody is purely good or evil.

MORAL GRAYNESS: no obvious hero. Engineer comment wars — "OP was wrong", "no, the
sister was", "the parents caused this".

PACING: max three sentences per event. Always move forward. Cut anything slow.
DESCRIPTION: minimal. Not "the majestic crimson sunset" — just "It was almost dark."
DIALOGUE: sparse, only to raise tension.

TWIST: at least one real twist, and it must make EARLIER events make more sense
(the villain was protecting someone; the "cheater" wasn't cheating; the prank
became evidence; the anonymous texter was a parent). Never a random twist.

ENDINGS: justice, karma, revenge, confession, unexpected kindness, irony, regret,
cliffhanger, legal consequences, bittersweet. NEVER "happily ever after".

LANGUAGE: simple, natural, conversational, spoken English, reading level 12-16.
EMOTIONS: fear, embarrassment, jealousy, love, regret, shock, curiosity, anger,
justice — without melodrama.
VIRAL: end on something people argue about (Was it justified? Would you forgive
them? Would you keep the money?).

ORIGINALITY: every example in these rules is an ILLUSTRATION ONLY — never reuse
an example sentence, premise or character as your story. Invent a fresh one.

QUALITY TEST before answering — modern setting; first person; conflict in first
sentence; escalation every few sentences; one major twist; believable; fast; thin
description; moral ambiguity; satisfying ending; no fantasy; no worldbuilding; no
AI cliches; no filler. If any test fails, REWRITE before responding."""

DOCTRINE_SUMMARY = (
    "Story doctrine: modern real-life drama only (Reddit-confession style), "
    "first person, conflict in the very first sentence, escalation every 2-3 "
    "sentences, one twist that recontextualizes, morally grey, simple spoken "
    "English, ends on something people argue about. Fantasy/magic/kingdoms/"
    "sci-fi/worldbuilding are strictly forbidden."
)

# ---- mechanical enforcement ------------------------------------------------ #
_FANTASY = [
    "wizard", "magic", "magical", "dragon", "kingdom", "realm", "prophecy",
    "sorcer", "spell", "elf", "elves", "dwarf", "orc", "goblin", "knight",
    "sword", "throne", "crown", "castle", "medieval", "ancient civilization",
    "time travel", "alien", "spaceship", "galaxy", "parallel universe",
    "mytholog", "deity", "god of", "goddess", "immortal", "curse of",
    "enchanted", "artifact", "relic", "chosen one", "zelda", "minecraft lore",
    "empire", "warrior", "quest", "rune", "portal", "dimension", "wield",
]
_BAD_OPENERS = [
    "my name is", "when i was growing up", "it was a sunny", "imagine",
    "what if i told you", "in this video", "let me tell you about a time",
    "have you ever wondered", "picture this",
]
# Openers small local models love to parrot from prompt examples. Copying one
# means every video starts the same way, so they're rejected outright.
_CLICHE_OPENERS = [
    "roommate sold my laptop", "boss accidentally wired me",
    "dad kicked me out after believing my cousin", "sister stole my college fund",
    "teacher accused me of cheating", "girlfriend secretly tested my loyalty",
]


def violations(script: dict) -> list[str]:
    """Hard-rule breaches. Empty list = the script obeys the doctrine."""
    scenes = script.get("scenes") or []
    if not scenes:
        return ["no scenes"]
    text = " ".join(str(s.get("narration", "")) for s in scenes).lower()
    title = str(script.get("title", "")).lower()
    first = str(scenes[0].get("narration", "")).strip()
    fl = first.lower()
    out = []

    hits = sorted({w for w in _FANTASY if w in text or w in title})
    if hits:
        out.append(f"FANTASY/WORLDBUILDING words used (banned): {', '.join(hits[:8])}")
    if not re.search(r"\b(i|i'm|i've|my|me)\b", fl):
        out.append("opening is not FIRST PERSON")
    if any(fl.startswith(b) for b in _BAD_OPENERS):
        out.append(f"banned opener: '{first[:40]}...'")
    if any(c in fl for c in _CLICHE_OPENERS):
        out.append("you copied a stock example opening — invent an original "
                   "first line with a different person, object and situation")
    # conflict in the first line: needs a charged verb/noun, not scene-setting
    if len(first.split()) > 28:
        out.append("first sentence too long — hit the conflict immediately")
    return out
