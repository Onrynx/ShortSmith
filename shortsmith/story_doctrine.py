"""THE STORY DOCTRINE — craft standards for every script Shortsmith writes.

This used to be a template. It specified one arc (hook -> setup -> escalation ->
twist -> ending), demanded the conflict land in the first sentence in under 15
words, capped every event at three sentences, and mechanically REJECTED any
script whose last line didn't end in a question mark. The result was exactly what
you'd expect: every video came out the same shape and raced through its own plot.

It is now a set of PRINCIPLES, not a mould. What makes a story hold attention is
specificity, credibility and earned turns — none of which require a fixed
sequence of beats. The structure of any given story is chosen by the planner
(story_planner.py) to suit that story.

`violations()` still enforces hard rules mechanically, but only the ones that are
genuinely non-negotiable: the narration must suit the voice that will read it,
and it must not read as AI slop. It no longer dictates shape.
"""
from __future__ import annotations

import re

DOCTRINE = """YOU ARE WRITING A STORY SOMEONE WILL WATCH TO THE END.
What holds attention is not a formula. It is wanting to know what happens next,
to people who feel real, in a situation you believe.

CREDIBILITY IS THE WHOLE GAME. Every story must feel like it actually happened.
The viewer should be thinking "what would I have done" — never "this sounds
AI-generated". The fastest way to be believed is to be SPECIFIC: names, places,
objects, times, exact things people said. Vague writing is the tell.

GIVE THE AUDIENCE GROUND TO STAND ON. Before something matters, the viewer needs
to know who this is, where they are, and what normal looks like. A shock that
arrives before we care about anyone is noise. Establish with concrete detail —
a name, an object, a habit, one line of dialogue — not with general description.
This does NOT mean writing slowly. It means spending your seconds where they buy
something. When a beat has done its job, move.

LET THE STORY DECIDE ITS OWN SHAPE. Some stories open mid-disaster. Some open
quiet and turn. Some are a slow accumulation of small wrong things with no twist
at all. Some end on a question, some on an image, some on a consequence, some in
silence. Choose what this specific story needs. Do not reach for the same shape
every time — if it feels like a formula you have already seen, it is.

TURNS MUST BE EARNED. If there is a reveal, the information that makes it land
must already be on screen, hiding in plain sight. A twist that comes from nowhere
is a cheat. A twist that makes the viewer re-read earlier scenes is the good kind.
Not every story needs one.

PEOPLE, NOT FUNCTIONS. Characters lie, panic, hide things, overreact, apologise,
get defensive, do the wrong thing for a decent reason. Nobody is purely good or
evil. The best stories leave the audience genuinely arguing about who was wrong.

LANGUAGE: simple, natural, spoken English — the way a person actually talks.
Short sentences carry tension; vary the rhythm so it doesn't drone. Description
stays thin: "it was almost dark", not "the majestic crimson sunset". Dialogue is
sparse and does work when it appears.

NEVER: "imagine...", "what if I told you", "in this video", "let's dive in",
"little did they know", "join me", listicle framing, or any sentence that sounds
like narration ABOUT a story instead of the story itself.

ORIGINALITY: anything mentioned in these rules is an illustration, never a
template. Invent your own premise, cast and situation."""

FACTUAL_CRAFT = """YOU ARE EXPLAINING SOMETHING REAL. ACCURACY COMES FIRST.

The viewer should finish actually understanding the thing. Everything below
serves that, not the other way round.

BE TRUE. Never invent a number, a date, a name, a study or a quote. If you are
not sure, either leave it out or say plainly that it is uncertain or disputed.
A vaguer true sentence always beats a specific invented one.

NO CHARACTERS. There is no narrator with a life, no "I", no anecdote about
someone who discovered this. The subject is the thing itself.

MAKE IT CONCRETE. Abstractions slide off people. Anchor every idea to something
they can picture — a bathtub, a plane, a spinning merry-go-round — and keep the
same image running rather than swapping metaphors every line.

EARN ATTENTION HONESTLY. Open on the part that is genuinely surprising or
counter-intuitive, not on a manufactured cliffhanger. Curiosity is the hook.

ONE IDEA AT A TIME, in an order where each sentence depends on the last. If a
term needs defining, define it the moment it appears.

LANGUAGE: plain spoken English, short sentences, no jargon left unexplained and
no lecturing tone. Say "the water turns" before "angular momentum"."""

DOCTRINE_SUMMARY = (
    "Story doctrine: believable, specific, first-person storytelling. Establish "
    "who and where before things escalate, let each story choose its own shape "
    "and ending, earn every turn, keep people morally grey, and write in plain "
    "spoken English with no AI-narrator clichés."
)

# ---- mechanical enforcement ------------------------------------------------ #
# Only genuinely hard rules live here. Anything about SHAPE was removed — that is
# the planner's job, and enforcing it here is what made every story identical.

# True fantasy markers: these break a grounded modern-drama channel. They are NOT
# applied when the story is deliberately historical or lore — a documentary about
# an empire is not "AI slop", and rejecting it was a real bug.
_FANTASY = [
    "wizard", "magic", "magical", "dragon", "prophecy", "sorcer", "spell",
    "elf", "elves", "dwarf", "orc", "goblin", "enchanted", "chosen one",
    "time travel", "alien", "spaceship", "parallel universe", "deity",
    "goddess", "immortal", "portal", "rune", "zelda", "minecraft lore",
]
_BAD_OPENERS = [
    "my name is", "when i was growing up", "it was a sunny",
    "imagine", "what if i told you", "in this video",
    "let me tell you about a time", "have you ever wondered", "picture this",
]
# Openers small local models parrot straight out of prompt examples.
_CLICHE_OPENERS = [
    "roommate sold my laptop", "boss accidentally wired me",
    "dad kicked me out after believing my cousin", "sister stole my college fund",
    "teacher accused me of cheating", "girlfriend secretly tested my loyalty",
]
# The narration is read by a MALE voice. This is a hardware fact, not a style
# choice, so it stays enforced.
_FEMALE_CODED = ("my husband", "as a woman", "as a girl", "i'm a woman",
                 "i am a woman", "i'm a girl", "us girls",
                 "my wife and i are both women")


def _mentions(text: str, term: str) -> bool:
    """True if `term` starts a word in `text`.

    Plain substring matching was finding "elf" inside "myself"/"himself" and
    "orc" inside "force"/"divorce" — so almost every first-person story was
    rejected for fantasy content that was not there, burning a rewrite call
    each time. Matching at a word START (but allowing suffixes) keeps prefix
    entries like "sorcer" -> "sorcery" working.
    """
    i = text.find(term)
    while i != -1:
        if i == 0 or not text[i - 1].isalpha():
            return True
        i = text.find(term, i + 1)
    return False


def violations(script: dict, mode: str = "real_life_drama",
               grounded: bool = True, first_person: bool = True) -> list[str]:
    """Hard-rule breaches only. Empty list = acceptable.

    `grounded` is False for historical/lore stories, which legitimately contain
    empires, battles and kings. `first_person` is False when the chosen narrator
    is an outside voice (a documentary chronicle, for instance).
    """
    scenes = script.get("scenes") or []
    if not scenes:
        return ["no scenes"]
    text = " ".join(str(s.get("narration", "")) for s in scenes).lower()
    title = str(script.get("title", "")).lower()
    first = str(scenes[0].get("narration", "")).strip()
    fl = first.lower()
    out = []

    if grounded and mode != "lore":
        hits = sorted({w for w in _FANTASY
                       if _mentions(text, w) or _mentions(title, w)})
        if hits:
            out.append(f"fantasy elements in a grounded story (banned here): "
                       f"{', '.join(hits[:6])}")
    if first_person and not re.search(r"\b(i|i'm|i've|my|me)\b", fl):
        out.append("the narrator was supposed to speak in first person")
    if any(fl.startswith(b) for b in _BAD_OPENERS):
        out.append(f"AI-narrator cliché opener: '{first[:40]}...'")
    if any(c in fl for c in _CLICHE_OPENERS):
        out.append("you copied a stock example opening — invent an original "
                   "first line with a different person, object and situation")
    if first_person:
        fem = [p for p in _FEMALE_CODED if p in text]
        if fem:
            out.append(f"narrator reads as FEMALE ({', '.join(fem[:3])}) — the voice "
                       "reading this is male, so write the narrator as a man")
    return out
