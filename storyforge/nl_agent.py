"""Natural-language layer for the Discord bot — one LLM call per message.

Routes plain English through the SAME free local brain StoryForge already uses
(Odysseus /v1 -> Ollama -> Gemini). The model returns strict JSON:
  {"say": "<reply>", "action": null | {"name": "...", "args": {...}}}
We execute the action via control.py and append the result. Zero paid services.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from collections import deque

from . import control
from .config import format_spec, load_channel
from .llm import complete, select_model
from .notify import nightly_digest
from .story_doctrine import DOCTRINE_SUMMARY
from .utils import log

# ---- actions the model may call (kept tiny to keep prompts/tokens tiny) ---- #
SAFE = {"status", "brain", "pending", "review", "digest", "feed"}
ADMIN = {"approve", "reject", "run"}

_SYS = """You are Valentine, the OWNER-OPERATOR of StoryForge, the user's own
open-source YouTube Shorts factory running on THEIR laptop. Nothing about it is
secret or proprietary — you built it together. NEVER refuse to explain any part
of it. Never say "I can't provide details". You are the interface that runs it.

SYSTEM MAP (all TRUE — explain any of it freely; do NOT add facts not listed here):
- On-demand only. A short is made ONLY when the user asks. There is NO schedule,
  NO daily quota, and NO "night/day" lanes to manage — ignore any such idea.
- Output: one vertical Short per request (1080x1920, ~2.5-3 min, hard-capped under
  YouTube's 3-minute Shorts limit), word-by-word burned captions,
  thumbnail. Saved to output/<channel>/pending/ for the user to approve, then
  approved/. Nothing is uploaded anywhere until the user does it.
- Channels: gossip_stories (fictional viral storytime drama — the MAIN one) and
  gaming_lore. ALL scripts are FICTION crafted to hook; never real people/events.
- Pipeline (storyforge/nodes/, 5 steps, runs on the user's laptop):
  script -> voice -> canvas -> cutter -> herald.
  * script: TWO steps. First a PLANNER develops the creator's idea — however
    vague — into a concrete premise, cast, setting and a beat sheet, choosing the
    structure that suits that particular story (not a fixed formula) and deciding
    how many seconds each beat deserves. Then a WRITER writes the narration to
    those beats. An anti-repeat memory keeps every story unique. Runs on the
    local AI (Ollama qwen2.5:14b); Gemini only if Ollama is down.
    The creator can give a gist ("a guy finds something strange in his house"),
    a theme ("revolve it around loneliness"), a real historical subject, or an
    exact opening line — all of those work, and vagueness is fine.
    They can also ask for a length ("make it 45 seconds") and the system plans,
    measures the real narration audio, and revises the story to actually fit.
  * voice: Kokoro "am_adam" — free, local, the default. The user picks a style in
    chat: "adam" (deep narrator) or "squeaky" (high-pitched, faster). ElevenLabs is
    used only if the user set a key.
  * canvas: the user's own gameplay clips in assets/backgrounds/ play behind the
    narration — a RANDOM clip and RANDOM section each time, auto-cropped to 9:16.
    If that folder is empty it falls back to an abstract animated background.
  * cutter: FFmpeg assembles narration + captions into the
    final vertical video.
  * herald: title/description/tags + the AI-disclosure line + thumbnail. The human
    approval gate is always on.
- 100% free and local by default; cloud keys (Gemini, ElevenLabs) are optional.
- Interface: this Discord bot (you). !commands are fast paths; plain English works.
- Repo: run.py CLI, storyforge/ package, config/*.yaml per channel,
  .storyforge_state/ runtime state, scripts/install_autostart.py for setup.

STORY DOCTRINE (you enforce this on every script): """+DOCTRINE_SUMMARY+"""

Be friendly, concrete and BRIEF (this is Discord). Answer the question actually
asked. When something actionable is wanted, pick ONE action.

HARD RULES:
- LIVE STATE (prepended to each message) is the ONLY source of truth about what is
  rendering. For "how long / time left / is it done / what's happening / continue",
  report ONLY what LIVE STATE says. NEVER invent lanes, stages, schedules, ETAs, or
  "the day is in motion" — if LIVE STATE says idle, say nothing is rendering.
- NEVER write a story or script into the chat. Scripts only ever go into the video.
- You do NOT start renders yourself — the app asks the voice question and renders.

Actions (name: args):
 status:{} brain:{} pending:{} digest:{} feed:{}
 review:{n:int}  approve:{n:int}  reject:{n:int}   (n = number from the pending list)

Reply ONLY strict JSON: {"say":"...", "action":null or {"name":"...","args":{...}}}
No action for greetings/explanations — answer fully in "say"."""

_hist: dict[int, deque] = {}


def _exec(name: str, args: dict) -> str:
    """Run an action, return a compact result string for Discord."""
    try:
        if name == "status":
            return _render_progress()
        if name == "brain":
            o = control.odysseus_status()
            b = o["storyforge_brain"]
            return f"brain: {b['backend']}/{b['model']} — {b['reason']}"
        if name == "pending":
            items = control.list_pending()
            return "\n".join(f"`{i+1}` **{m.get('title')}** — {m.get('channel','')}"
                             for i, m in enumerate(items[:10])) or "nothing pending"
        if name in ("review", "approve", "reject"):
            items = control.list_pending()
            n = int(args.get("n", 0))
            if not (1 <= n <= len(items)):
                return f"there's no pending item #{n} (use the pending list numbers)"
            iid = items[n - 1]["id"]
            if name == "approve":
                r = control.approve(iid)
                return "✅ approved → approved/" if r.get("ok") else f"❌ {r.get('error')}"
            if name == "reject":
                r = control.reject(iid)
                return "🗑️ rejected & deleted" if r.get("ok") else f"❌ {r.get('error')}"
            m = control.get_pending(iid)
            sc = "\n".join(f"{i+1}. {'⭐' if s.get('hero') else '·'} {s.get('narration','')}"
                           for i, s in enumerate(m.get("scenes") or []))
            return f"**{m.get('title')}**\n{sc[:1200]}"
        if name == "run":
            # The LLM may NEVER start a render on its own — it used to skip the
            # voice question. Starting videos is deterministic (fast_route) only.
            return _VOICE_Q
        if name == "digest":
            return f"```\n{nightly_digest()[:1500]}\n```"
        if name == "feed":
            icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
            return "\n".join(f"{icon.get(e.get('severity'),'•')} {e.get('stage')}: "
                             f"{e.get('message','')[:90]}" for e in control.events(8)) or "no events"
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ {name} failed: {exc}"
    return f"unknown action {name}"


# --------------------------------------------------------------------------- #
# DETERMINISTIC FAST ROUTER — runs BEFORE the LLM.
# Small local models love to "helpfully" write a script into the chat instead of
# starting a render. Every common command is therefore handled in plain Python so
# the model is never in the critical path and simply cannot improvise.
# --------------------------------------------------------------------------- #
_VIDEO_RE = re.compile(
    r"\b(make|create|generate|start|run|produce|build|do|need|want|give|use|lets|let's)\b"
    r"[^.?!]{0,80}\b(short|shorts|video|vid|reel|clip|story|storytime|script|one)\b", re.I)
_ANOTHER_RE = re.compile(r"\b(another|one more|do it again|next one)\b", re.I)
_ADAM_RE = re.compile(r"\b(adam|deep|normal|default|male|regular|1|first|one)\b", re.I)
_SQUEAK_RE = re.compile(r"\b(squeak\w*|chipmunk|high[\s-]?pitch\w*|helium|funny|2|second|two)\b", re.I)
_STATUS_RE = re.compile(r"^\s*(status|what'?s (the )?status|how'?s it going|progress)\s*\??\s*$", re.I)
_PENDING_RE = re.compile(r"^\s*(pending|what'?s (waiting|pending)|list( videos)?)\s*\??\s*$", re.I)
_APPROVE_RE = re.compile(r"\b(approve|keep|accept)\b\s*#?(\d+)?", re.I)
_REJECT_RE = re.compile(r"\b(reject|delete|bin|discard|trash)\b\s*#?(\d+)?", re.I)
_SCRIPT_REQ_RE = re.compile(r"\b(write|show|give|read)\b.{0,20}\b(script|story)\b", re.I)
QUOTED_RE = "[\"“]([^\"”]{8,300})[\"”]"
# "...starting with my family kicked me out" / "start it with X" / "opening line X"
STARTER_RE = (r"(?:start(?:ing|s)?(?:\s+it)?\s+with|begins?\s+with|opening(?:\s+line)?|"
              r"first\s+line|hook[:\s])[:\s]+(.{5,300})$")
# Split into THEME-style and SUBJECT-style connectors, because which one the user
# used changes how the planner must treat the idea: a theme has to shape the whole
# story, a subject is just what it's about. Collapsing them lost that distinction.
THEME_RE = (r"(?:^|\s)(?:revolv\w*\s+(?:it\s+|the\s+story\s+)?around|"
            r"cent(?:er|re)\w*\s+(?:it\s+|the\s+story\s+)?(?:on|around)|"
            r"theme\s+of|all\s+about|really\s+about|explores?)"
            r"[:\s]+(.{4,300})$")
TOPIC_RE = (r"(?:^|\s)(?:about|where|on the topic of|starter|story|idea|"
            r"based\s+on|involving|featuring|with)"
            r"[:\s]+(.{6,300})$")
# leftover connective words at the head of an extracted brief
_LEAD_JUNK = re.compile(r"^(?:about|around|on|of|with|involving|that|it|one|is)\b[\s,]*",
                        re.I)

# time-left / progress / "is it done" / "continue" — answered from real state,
# NEVER handed to the LLM (it invents fake lanes and stages).
_PROGRESS_RE = re.compile(
    r"\b(how\s+(much\s+)?(time|long)|time\s+left|eta|is\s+it\s+(done|ready|finished|rendering|going)|"
    r"still\s+(going|rendering|working|cooking)|how\s+far|are\s+(we|you)\s+(done|there)|"
    r"almost\s+(done|there)|any\s+update|what'?s\s+(happening|going\s+on|the\s+status)|"
    r"what\s+stage|which\s+stage|where\s+(is\s+it|are\s+we)\s+(at|now)?|what'?s\s+it\s+doing|"
    r"continue(\s+the)?(\s+video|\s+render(ing)?|\s+process)?|keep\s+going|hurry|"
    r"(done|ready|finished)\s+yet)\b", re.I)
# "show me a sample / preview / what's it written so far"
_SAMPLE_RE = re.compile(
    r"\b(sample|preview|sneak\s*peek|what.{0,12}(written|got)\s+so\s+far|"
    r"show\s+me\s+(what|some|a\s+bit)|snippet|excerpt|first\s+few\s+lines)\b", re.I)

# channel_id -> pending video request awaiting a voice choice
_awaiting: dict[int, dict] = {}

_VOICE_Q = ("Which voice?\n"
            "**1) Adam** — deep narrator (default)\n"
            "**2) Squeaky** — high-pitched chipmunk\n"
            "_Reply `adam` or `squeaky`._")


# "45 seconds", "a minute and a half", "2 min", "90s"
_DUR_RE = re.compile(
    r"\b(?:(\d+(?:\.\d+)?)\s*(?:-|to)\s*)?(\d+(?:\.\d+)?)\s*"
    r"(seconds?|secs?|s|minutes?|mins?|m)\b", re.I)
_DUR_WORDS = {"half a minute": 30, "a minute and a half": 90, "minute and a half": 90,
              "a minute": 60, "one minute": 60, "two minutes": 120,
              "three minutes": 180, "90 seconds": 90}
# command scaffolding that is NOT part of the creative brief
_CMD_STRIP = re.compile(
    r"\b(please|can you|could you|i want|i'd like|i need|lets|let's|now|asap|"
    r"make|create|generate|produce|build|do|give me|another|one more|new|a|an|the|"
    r"short|shorts|video|vid|reel|clip|storytime|for me|okay|ok)\b", re.I)


def _extract_duration(text: str) -> float | None:
    """Pull an explicit requested length, in seconds, out of the message."""
    low = text.lower()
    for phrase, secs in _DUR_WORDS.items():
        if phrase in low:
            return float(secs)
    m = _DUR_RE.search(text)
    if not m:
        return None
    lo, hi, unit = m.group(1), m.group(2), m.group(3).lower()
    try:
        val = float(hi)
        if lo:                                   # "2-3 minutes" -> take the midpoint
            val = (float(lo) + val) / 2.0
    except ValueError:
        return None
    if unit.startswith("m"):
        val *= 60.0
    if not (5 <= val <= 600):                    # ignore nonsense like "video 1"
        return None
    return val


def _extract_topic(text: str) -> str:
    """Understand what the user actually wants the story to be.

    An exact quoted line or an explicit "starting with ..." is authoritative and
    is passed through verbatim. Otherwise we strip the command scaffolding and
    keep EVERYTHING else as the brief — "a revenge plot, no deviations" is a real
    creative instruction, and the old version threw it away because it didn't
    match an `about X` pattern. The planner is what interprets it.
    """
    def _clean(s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip(" .,-")
        return _LEAD_JUNK.sub("", s).strip(" .,-")

    m = re.search(QUOTED_RE, text)                 # "...an exact opening line..."
    if m:
        return m.group(1).strip()
    m = re.search(STARTER_RE, text, re.I | re.S)   # ...starting with X / begins with X
    if m:
        return _clean(m.group(1))
    m = re.search(THEME_RE, text, re.I | re.S)     # ...revolve around X / theme of X
    if m:
        # Keep the intent word so the planner treats this as a THEME to build the
        # whole story around, not merely as a subject to mention.
        return f"the story should revolve around {_clean(m.group(1))}"
    m = re.search(TOPIC_RE, text, re.I | re.S)     # ...about X / based on X
    if m:
        return _clean(m.group(1))
    # Fall back to "whatever is left once the command words are gone".
    rest = _DUR_RE.sub(" ", text)
    rest = _CMD_STRIP.sub(" ", rest)
    rest = re.sub(r"\b(adam|squeaky|deep|chipmunk)\b", " ", rest, flags=re.I)
    rest = re.sub(r"[^\w\s',.!?-]", " ", rest)
    rest = _clean(rest)
    return rest if len(rest.split()) >= 3 else ""


# --------------------------------------------------------------------------- #
# RENDER PROGRESS — reported from the on-disk status file, never guessed.
# --------------------------------------------------------------------------- #
_STAGE_ORDER = ["script", "voice", "canvas", "cutter", "herald"]
_STAGE_HUMAN = {
    "script": ("✍️", "writing the story (the slowest part — your local AI drafts then expands it)"),
    "voice":  ("🎙️", "recording the narration"),
    "canvas": ("🎮", "building the video over your gameplay background"),
    "cutter": ("✂️", "cutting it together — burning in the captions"),
    "herald": ("🖼️", "finishing up — title, thumbnail, disclosure"),
}


def _render_state() -> dict:
    """The current/last render's status dict (from the single render slot)."""
    try:
        return control.status().get("render") or {}
    except Exception:
        return {}


def _pending_count() -> int:
    try:
        return len(control.list_pending())
    except Exception:
        return 0


def _elapsed_min(st: dict) -> float | None:
    """Minutes since this render started, from the run_id timestamp (local clock)."""
    m = re.match(r"(\d{8}_\d{6})", str(st.get("run_id") or ""))
    if not m:
        return None
    try:
        t0 = _dt.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    return max(0.0, (_dt.datetime.now() - t0).total_seconds() / 60.0)


def _render_done(st: dict) -> bool:
    return st.get("pct") == 100.0 or "pending/" in str(st.get("message") or "")


def _live_render_phrase(s: dict) -> str:
    """One true fact for the LLM's LIVE STATE so it can't invent lanes/stages."""
    st = s.get("render") or {}
    stage = str(st.get("stage") or "").lower()
    pend = s.get("pending_count") or 0
    if stage in ("", "idle", "cancelled"):
        return (f"idle (nothing is rendering); {pend} video(s) waiting in !pending"
                if pend else "idle (nothing is rendering)")
    if stage == "failed":
        return "the last render FAILED"
    if _render_done(st):
        return "just finished — a video is waiting in !pending"
    step = _STAGE_ORDER.index(stage) + 1 if stage in _STAGE_ORDER else "?"
    return f"running (step {step}/5: {stage})"


def _ready_line() -> str:
    n = _pending_count()
    if n <= 0:
        return ("Nothing is rendering right now. Say **make me a short** to start one — "
                "I'll ask Adam or squeaky, then it renders in ~8–15 min and posts here.")
    return (f"✅ **Ready** — {n} finished video(s) are waiting for you. Say `!pending` to "
            "list them, then `!approve 1` to keep #1 or `!reject 1` to bin it.")


def _render_progress() -> str:
    """A smart, honest answer to 'how long left / is it done / what's happening'."""
    st = _render_state()
    stage = str(st.get("stage") or "").lower()
    msg = str(st.get("message") or "")
    if stage in ("", "idle", "cancelled"):
        return _ready_line()
    if stage == "failed":
        return (f"❌ The last render failed: _{msg[:180]}_\n"
                "Say **make me a short** to try again, or run "
                "`python scripts/install_autostart.py --doctor` to see why.")
    if _render_done(st):
        title = st.get("title")
        which = f" **{title}**" if title else ""
        return (f"✅ **Done** — the latest short{which} finished and is waiting in this "
                "channel / `!pending`. Say `!approve 1` to keep it or `!reject 1` to bin it.")
    icon, human = _STAGE_HUMAN.get(stage, ("⏳", stage))
    step = _STAGE_ORDER.index(stage) + 1 if stage in _STAGE_ORDER else "?"
    mins = _elapsed_min(st)
    when = f" — running **{mins:.0f} min** so far" if mins is not None else ""
    lines = [f"{icon} Still rendering — **step {step}/5**: {human}{when}."]
    # Real detail from the run itself, when the script stage has produced any.
    if st.get("logline"):
        lines.append(f"📖 It's writing: _{str(st['logline'])[:180]}_")
    if st.get("structure"):
        lines.append(f"🧩 Shape: **{st['structure']}** · {st.get('beats','?')} beats")
    if st.get("scenes_written"):
        lines.append(f"✍️ {st['scenes_written']}/{st.get('scenes_total','?')} scenes "
                     f"written ({st.get('words_written','?')} words)")
    lines.append("It posts here automatically when it's done (usually **8–15 min**), "
                 "so there's no need to wait in chat.")
    return "\n".join(lines)


def _sample_reply() -> str:
    """Answer 'show me a sample' with the real opening lines, if they exist yet."""
    st = _render_state()
    opening = str(st.get("opening") or "").strip()
    logline = str(st.get("logline") or "").strip()
    if opening:
        return (f"📝 Here's the opening it has written so far:\n\n> {opening[:600]}\n\n"
                f"_({st.get('scenes_written','?')}/{st.get('scenes_total','?')} scenes "
                "done — the full script comes with the video.)_")
    if logline:
        return (f"It's still writing, but here's the story it planned:\n\n> {logline[:400]}\n\n"
                "_The opening lines will be available in a minute or two._")
    if _render_progress().startswith("✅"):
        return ("The video's finished — use `!review 1` to read its full script and "
                "watch it.")
    return ("Nothing written yet — it's still planning the story. Ask again in a "
            "minute and I'll show you the opening lines.")


def _pick_channel(text: str) -> str:
    return "gaming_lore" if re.search(r"\b(gaming|game|lore)\b", text, re.I) else "gossip_stories"


def _duration_label(channel: str) -> str:
    """Real length window from the channel config, e.g. '2.5–3 min'."""
    try:
        spec = format_spec(load_channel(channel))
        lo = (spec.get("min_seconds") or spec.get("target_seconds") or 150) / 60.0
        hi = (spec.get("max_seconds") or 178) / 60.0
        fmt = lambda x: f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}"
        return f"{fmt(lo)}–{fmt(hi)} min"
    except Exception:
        return "2.5–3 min"


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{int(s)}s"
    m, r = divmod(int(round(s)), 60)
    return f"{m}m" if not r else f"{m}m{r:02d}s"


def _start_video(channel: str, voice: str, is_admin: bool, topic: str = "",
                 seconds: float | None = None) -> str:
    if not is_admin:
        return "🔒 only the owner can start renders on this server."
    r = control.run_now(channel, control.RENDER_SLOT, False, voice=voice,
                        topic=topic, seconds=seconds)
    if not r.get("ok"):
        return f"❌ {r.get('error')}"
    extra = f"\n📌 Your brief: _{topic[:180]}_" if topic else ""
    length = _fmt_secs(seconds) if seconds else _duration_label(channel)
    return (f"▶️ Making a **{channel.replace('_', ' ')}** short — **{voice}** voice, "
            f"{length}.{extra}\n"
            "~8–15 min to render. It'll post here automatically when it's done — "
            "ask me **how long left** any time.")


# cancel / never mind — clears a pending voice question and stops a live render
_CANCEL_RE = re.compile(
    r"^\s*(cancel|abort|stop|halt|nvm|never\s?mind|forget\s+it|drop\s+it|scrap\s+it|"
    r"leave\s+it|no\s+thanks?|don'?t\s+(make|bother))"
    r"(\s+(it|that|this|the\s+(request|render|video|short)s?|them|everything))?\s*[!.\s]*$",
    re.I)
_CANCEL_WORD = re.compile(r"\b(cancel|abort|nvm|never\s?mind|forget\s+it)\b", re.I)
# impatience about an EXISTING render/finished video (don't start a second one)
_IMPATIENT_RE = re.compile(
    r"\b(where'?s|where\s+is|hurry|come\s+on|already|still\s+(there|going|waiting|rendering)|"
    r"give\s+(me\s+)?(the|my)\s+(video|short))\b", re.I)


def fast_route(text: str, channel_id: int, is_admin: bool) -> str | None:
    """Handle a message deterministically, or return None to let the LLM chat."""
    t = text.strip()

    # 0) Cancel / never mind — kills a pending voice question AND a live render.
    cancel_hit = bool(_CANCEL_RE.match(t)) or bool(re.search(r"\bcancel\b.*\brequest", t, re.I))
    if not cancel_hit and _awaiting.get(channel_id) and _CANCEL_WORD.search(t):
        cancel_hit = True
    if cancel_hit:
        had_q = _awaiting.pop(channel_id, None) is not None
        try:
            running = is_admin and control.render_in_progress()
        except Exception:
            running = False
        if running:
            control.cancel_render()
            return "🛑 Stopped the render in progress. Say **make me a short** for another."
        if had_q:
            return "👍 Cancelled — I won't make that one. Say **make me a short** when you're ready."
        return "Nothing to cancel right now — nothing is queued or rendering."

    # 1) Answering the voice question we asked
    pend = _awaiting.get(channel_id)
    if pend:
        if _SQUEAK_RE.search(t):
            _awaiting.pop(channel_id, None)
            return _start_video(pend["channel"], "squeaky", is_admin,
                                pend.get("topic", ""), pend.get("seconds"))
        if _ADAM_RE.search(t):
            _awaiting.pop(channel_id, None)
            return _start_video(pend["channel"], "adam", is_admin,
                                pend.get("topic", ""), pend.get("seconds"))
        if len(t) < 25:                       # a short non-answer: re-ask, don't ramble
            return _VOICE_Q
        _awaiting.pop(channel_id, None)       # they changed the subject

    # 1b) Impatient about an existing render / finished video -> report it, don't
    #     kick off a whole new render.
    if _IMPATIENT_RE.search(t):
        try:
            busy = control.render_in_progress()
        except Exception:
            busy = False
        if busy or _pending_count() > 0:
            return _render_progress()

    # 1c) "show me the script of #2" refers to an EXISTING video — that's a read,
    #     not a new render, even though it shares verbs with a render request.
    if _SCRIPT_REQ_RE.search(t) and re.search(
            r"\b(\d+|that|this|the last|previous|pending|finished|it)\b", t, re.I):
        return ("I don't write scripts in chat — the pipeline writes them into the "
                "video. Use `!review 1` to read the script of a finished video, or "
                "say **make me a short** to create a new one.")

    # 2) "make me a short" (+ optional voice in the same sentence)
    if _VIDEO_RE.search(t) or (_ANOTHER_RE.search(t) and re.search(r"\b(short|video|one)\b", t, re.I)):
        channel = _pick_channel(t)
        topic = _extract_topic(t)          # the creative brief, in the user's words
        secs = _extract_duration(t)        # "make it 45 seconds"
        if _SQUEAK_RE.search(t):
            return _start_video(channel, "squeaky", is_admin, topic, secs)
        if re.search(r"\b(adam|deep voice|normal voice)\b", t, re.I):
            return _start_video(channel, "adam", is_admin, topic, secs)
        _awaiting[channel_id] = {"channel": channel, "topic": topic, "seconds": secs}
        return _VOICE_Q

    # 3) "how long left / what stage / show me a sample" -> REAL state.
    #    This must beat the LLM, which invents stages and has no way to see a run.
    if _SAMPLE_RE.search(t):
        return _sample_reply()
    if _PROGRESS_RE.search(t) or _STATUS_RE.match(t):
        return _render_progress()

    # 4) Never write a script into chat — that's the pipeline's job
    if _SCRIPT_REQ_RE.search(t):
        return ("I don't write scripts in chat — the pipeline writes them into the "
                "video. Say **make me a short** to create one, or `!review 1` to read "
                "the script of a finished video.")

    # 5) Cheap exact commands
    if _PENDING_RE.match(t):
        return _exec("pending", {})
    m = _APPROVE_RE.search(t)
    if m and not _VIDEO_RE.search(t):
        return _exec("approve", {"n": int(m.group(2) or 1)})
    m = _REJECT_RE.search(t)
    if m and not _VIDEO_RE.search(t):
        return _exec("reject", {"n": int(m.group(2) or 1)})
    return None


def _sanitize(reply: str) -> str:
    """Strip model rambling: no scripts, no essays, no invented video content."""
    lines = [l for l in reply.splitlines() if l.strip()]
    numbered = sum(1 for l in lines if re.match(r"^\s*(\d+[\.\)]|scene\s*\d)", l, re.I))
    if numbered >= 3:      # it wrote a script instead of answering
        return ("I don't write stories in chat — say **make me a short** and I'll "
                "render one properly.")
    out = "\n".join(lines)
    return (out[:700] + "…") if len(out) > 700 else out


def handle(text: str, channel_id: int, is_admin: bool = True) -> str:
    """One user message -> one reply. Deterministic router first, LLM only for chat."""
    routed = fast_route(text, channel_id, is_admin)
    if routed is not None:
        return routed
    mi = select_model(False)
    if mi["backend"] == "mock":
        return ("My brain is offline (no Odysseus/Ollama reachable). "
                "Commands still work: try `!help`.")
    # Chat stays on a FAST model; the big model is reserved for script writing.
    if mi["backend"] == "ollama":
        from .utils import env as _env
        mi = {**mi, "model": _env("OLLAMA_CHAT_MODEL", "qwen3:8b") or "qwen3:8b"}
    h = _hist.setdefault(channel_id, deque(maxlen=8))
    convo = "\n".join(f"{r}: {t}" for r, t in h) or "(new conversation)"
    try:
        s = control.status()
        live = (f"LIVE STATE (only true facts — invent nothing else): "
                f"brain={mi['backend']}/{mi['model']}; "
                f"videos waiting for review={s.get('pending_count')}; "
                f"render right now={_live_render_phrase(s)}.")
    except Exception:
        live = "LIVE STATE: unavailable"
    # While a render is running, the local GPU is busy writing the story, so a
    # chat call queues behind it. Give it much longer before giving up, rather
    # than timing out and looking broken exactly when the user is most curious.
    try:
        busy = control.render_in_progress()
    except Exception:
        busy = False
    try:
        raw = complete(_SYS, f"{live}\n\nRecent chat:\n{convo}\n\nuser: {text}", mi,
                       json_mode=True, temperature=0.4, timeout=420 if busy else 150)
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception as exc:  # noqa: BLE001
        log("warn", f"nl_agent chat failed: {exc}")
        # Never answer with a shrug when we have real facts on disk.
        if busy:
            return (_render_progress() +
                    "\n\n_(My chat brain is tied up writing the story right now, so "
                    "I'm answering from the render itself.)_")
        return ("I couldn't get a reply out of the local model just now. The "
                "commands all still work — `!status`, `!pending`, `!review 1` — "
                "and **make me a short** starts a video.")
    say = str(data.get("say") or "")
    act = data.get("action")
    out = say
    if isinstance(act, dict) and act.get("name"):
        name = str(act["name"])
        if name == "run":
            # Route every render request back through the deterministic path so
            # the voice question is ALWAYS asked, whatever the model decided.
            args = act.get("args") or {}
            ch = args.get("channel") or _pick_channel(text)
            _awaiting[channel_id] = {"channel": ch, "topic": _extract_topic(text),
                                     "seconds": _extract_duration(text)}
            return _VOICE_Q
        if name in ADMIN and not is_admin:
            out = f"{say}\n🔒 `{name}` is owner-only on this server."
        elif name in SAFE | ADMIN:
            out = f"{say}\n{_exec(name, act.get('args') or {})}".strip()
    out = _sanitize(out)          # no scripts / essays / invented content in chat
    h.append(("user", text[:200]))
    h.append(("valentine", out[:200]))
    return out[:1990]
