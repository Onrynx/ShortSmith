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
ADMIN = {"approve", "reject", "run", "daily", "lanes"}

_SYS = """You are Valentine, the OWNER-OPERATOR of StoryForge, the user's own
open-source YouTube Shorts factory running on THEIR laptop. Nothing about it is
secret or proprietary — you built it together. NEVER refuse to explain any part
of it. Never say "I can't provide details". You are the interface that runs it.

SYSTEM MAP (all TRUE — explain any of it freely; do NOT add facts not listed here):
- On-demand only. A short is made ONLY when the user asks. There is NO schedule,
  NO daily quota, and NO "night/day" lanes to manage — ignore any such idea.
- Output: one vertical Short per request (1080x1920, ~2.5-3 min, hard-capped under
  YouTube's 3-minute Shorts limit), word-by-word burned captions, mood music,
  thumbnail. Saved to output/<channel>/pending/ for the user to approve, then
  approved/. Nothing is uploaded anywhere until the user does it.
- Channels: gossip_stories (fictional viral storytime drama — the MAIN one) and
  gaming_lore. ALL scripts are FICTION crafted to hook; never real people/events.
- Pipeline (storyforge/nodes/, 5 steps, runs on the user's laptop):
  script -> voice -> canvas -> cutter -> herald.
  * script: the local AI (Ollama qwen2.5:14b) writes it with a chunked-continuation
    loop + the enforced story doctrine + an anti-repeat memory so every script is
    unique. (Gemini is used only if Ollama is down.)
  * voice: Kokoro "am_adam" — free, local, the default. The user picks a style in
    chat: "adam" (deep narrator) or "squeaky" (high-pitched, faster). ElevenLabs is
    used only if the user set a key.
  * canvas: the user's own gameplay clips in assets/backgrounds/ play behind the
    narration — a RANDOM clip and RANDOM section each time, auto-cropped to 9:16.
    If that folder is empty it falls back to an abstract animated background.
  * cutter: FFmpeg assembles narration + captions + mood-matched music into the
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
            s = control.status()
            ln = [f"**{l}**: {(s['lanes'].get(l) or {}).get('stage','idle')} — "
                  f"{(s['lanes'].get(l) or {}).get('message','')[:100]}" for l in ("night", "day")]
            a = s["assignment"]
            ln.append(f"lanes: night={a.get('night')} day={a.get('day')} · "
                      f"pending: {s['pending_count']}")
            return "\n".join(ln)
        if name == "brain":
            o = control.odysseus_status()
            b = o["storyforge_brain"]
            return f"brain: {b['backend']}/{b['model']} — {b['reason']}"
        if name == "pending":
            items = control.list_pending()
            return "\n".join(f"`{i+1}` **{m.get('title')}** — {m.get('channel')}/{m.get('lane')}"
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
        if name == "daily":
            r = control.run_scheduler_daily(bool(args.get("mock", False)))
            return f"▶ daily cycle started (pid {r['pid']})"
        if name == "lanes":
            r = control.set_lanes(args.get("night", ""), args.get("day", ""))
            a = r["assignment"]
            return f"🌗 night: **{a.get('night')}** · day: **{a.get('day')}**"
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
TOPIC_RE = (r"(?:^|\s)(?:about|where|on the topic of|starter|story|idea)"
            r"[:\s]+(.{6,300})$")

# time-left / progress / "is it done" / "continue" — answered from real state,
# NEVER handed to the LLM (it invents fake lanes and stages).
_PROGRESS_RE = re.compile(
    r"\b(how\s+(much\s+)?(time|long)|time\s+left|eta|is\s+it\s+(done|ready|finished|rendering|going)|"
    r"still\s+(going|rendering|working|cooking)|how\s+far|are\s+(we|you)\s+(done|there)|"
    r"almost\s+(done|there)|any\s+update|what'?s\s+(happening|going\s+on|the\s+status)|"
    r"continue(\s+the)?(\s+video|\s+render(ing)?|\s+process)?|keep\s+going|hurry|"
    r"(done|ready|finished)\s+yet)\b", re.I)

# channel_id -> pending video request awaiting a voice choice
_awaiting: dict[int, dict] = {}

_VOICE_Q = ("Which voice?\n"
            "**1) Adam** — deep narrator (default)\n"
            "**2) Squeaky** — high-pitched chipmunk\n"
            "_Reply `adam` or `squeaky`._")


def _extract_topic(text: str) -> str:
    """Pull a story starter out of the request, if the user gave one."""
    m = re.search(QUOTED_RE, text)                 # "...an exact opening line..."
    if m:
        return m.group(1).strip()
    m = re.search(STARTER_RE, text, re.I | re.S)   # ...starting with X / begins with X
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip(" .")
    m = re.search(TOPIC_RE, text, re.I | re.S)     # ...about X / starter: X
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip(" .")
    return ""


# --------------------------------------------------------------------------- #
# RENDER PROGRESS — reported from the on-disk status file, never guessed.
# --------------------------------------------------------------------------- #
_STAGE_ORDER = ["script", "voice", "canvas", "cutter", "herald"]
_STAGE_HUMAN = {
    "script": ("✍️", "writing the story (the slowest part — your local AI drafts then expands it)"),
    "voice":  ("🎙️", "recording the narration"),
    "canvas": ("🎮", "building the video over your gameplay background"),
    "cutter": ("✂️", "cutting it together — captions + music"),
    "herald": ("🖼️", "finishing up — title, thumbnail, disclosure"),
}


def _render_state() -> dict:
    """The current/last render's status dict (renders always use the 'night' lane)."""
    try:
        return control.status()["lanes"].get("night") or {}
    except Exception:
        return {}


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
    st = (s.get("lanes") or {}).get("night") or {}
    stage = str(st.get("stage") or "").lower()
    if not stage or stage == "idle":
        return "idle (nothing is rendering)"
    if stage == "failed":
        return "the last render FAILED"
    if _render_done(st):
        return "just finished — a video is waiting in !pending"
    step = _STAGE_ORDER.index(stage) + 1 if stage in _STAGE_ORDER else "?"
    return f"running (step {step}/5: {stage})"


def _render_progress() -> str:
    """A smart, honest answer to 'how long left / is it done / what's happening'."""
    st = _render_state()
    stage = str(st.get("stage") or "").lower()
    msg = str(st.get("message") or "")
    if not stage or stage == "idle":
        return ("Nothing is rendering right now. Say **make me a short** to start one — "
                "I'll ask Adam or squeaky, then it renders in ~8–15 min and posts here.")
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
    return (f"{icon} Still rendering — **step {step}/5**: {human}{when}.\n"
            "The whole thing usually takes **8–15 min** and posts here automatically "
            "when it's done, so there's no need to wait in chat.")


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


def _start_video(channel: str, voice: str, is_admin: bool, topic: str = "") -> str:
    if not is_admin:
        return "🔒 only the owner can start renders on this server."
    r = control.run_now(channel, "night", False, voice=voice, topic=topic)
    if not r.get("ok"):
        return f"❌ {r.get('error')}"
    extra = f"\n📌 Your starter: _{topic[:140]}_" if topic else ""
    return (f"▶️ Making a **{channel.replace('_', ' ')}** short — **{voice}** voice, "
            f"{_duration_label(channel)}.{extra}\n"
            "~8–15 min to render. It'll post here automatically when it's done — "
            "ask me **how long left** any time.")


def fast_route(text: str, channel_id: int, is_admin: bool) -> str | None:
    """Handle a message deterministically, or return None to let the LLM chat."""
    t = text.strip()

    # 1) Answering the voice question we asked
    pend = _awaiting.get(channel_id)
    if pend:
        if _SQUEAK_RE.search(t):
            _awaiting.pop(channel_id, None)
            return _start_video(pend["channel"], "squeaky", is_admin, pend.get("topic", ""))
        if _ADAM_RE.search(t):
            _awaiting.pop(channel_id, None)
            return _start_video(pend["channel"], "adam", is_admin, pend.get("topic", ""))
        if len(t) < 25:                       # a short non-answer: re-ask, don't ramble
            return _VOICE_Q
        _awaiting.pop(channel_id, None)       # they changed the subject

    # 2) "make me a short" (+ optional voice in the same sentence)
    if _VIDEO_RE.search(t) or (_ANOTHER_RE.search(t) and re.search(r"\b(short|video|one)\b", t, re.I)):
        channel = _pick_channel(t)
        topic = _extract_topic(t)          # optional story starter from the user
        if _SQUEAK_RE.search(t):
            return _start_video(channel, "squeaky", is_admin, topic)
        if re.search(r"\b(adam|deep voice|normal voice)\b", t, re.I):
            return _start_video(channel, "adam", is_admin, topic)
        _awaiting[channel_id] = {"channel": channel, "topic": topic}
        return _VOICE_Q

    # 3) "how long left / is it done / continue / how's it going" -> REAL state.
    #    This must beat the LLM, which invents fake night/day lanes and stages.
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
    try:
        raw = complete(_SYS, f"{live}\n\nRecent chat:\n{convo}\n\nuser: {text}", mi,
                       json_mode=True, temperature=0.4, timeout=150)
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception as exc:  # noqa: BLE001
        log("warn", f"nl_agent parse: {exc}")
        return "Sorry, my brain glitched — try again or use `!help` commands."
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
            _awaiting[channel_id] = {"channel": ch, "topic": _extract_topic(text)}
            return _VOICE_Q
        if name in ADMIN and not is_admin:
            out = f"{say}\n🔒 `{name}` is owner-only on this server."
        elif name in SAFE | ADMIN:
            out = f"{say}\n{_exec(name, act.get('args') or {})}".strip()
    out = _sanitize(out)          # no scripts / essays / invented content in chat
    h.append(("user", text[:200]))
    h.append(("valentine", out[:200]))
    return out[:1990]
