"""Natural-language layer for the Discord bot — one LLM call per message.

Routes plain English through the SAME free local brain StoryForge already uses
(Odysseus /v1 -> Ollama -> Gemini). The model returns strict JSON:
  {"say": "<reply>", "action": null | {"name": "...", "args": {...}}}
We execute the action via control.py and append the result. Zero paid services.
"""
from __future__ import annotations

import json
from collections import deque

from . import control
from .llm import complete, select_model
from .notify import nightly_digest
from .utils import log

# ---- actions the model may call (kept tiny to keep prompts/tokens tiny) ---- #
SAFE = {"status", "brain", "pending", "review", "digest", "feed"}
ADMIN = {"approve", "reject", "run", "daily", "lanes"}

_SYS = """You are Valentine, the operator of StoryForge — a free, local, autonomous
YouTube Shorts factory (2 vertical AI Shorts/day, 7 agents, human approval gate).
Be friendly, brief (Discord chat), and honest. Answer questions about StoryForge
or chat normally. When the user asks for something actionable, pick ONE action.

Actions (name: args):
 status:{} brain:{} pending:{} digest:{} feed:{}
 review:{n:int}  approve:{n:int}  reject:{n:int}   (n = number from the pending list)
 run:{channel:"history_mysteries"|"gaming_lore", lane:"night"|"day", mock:bool}
 daily:{mock:bool}   lanes:{night:str, day:str}

Reply ONLY strict JSON: {"say":"...", "action":null or {"name":"...","args":{...}}}
No action for greetings/questions — just chat in "say"."""

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
            r = control.run_now(args.get("channel", ""), args.get("lane", "night"),
                                bool(args.get("mock", True)))
            return f"▶ started (pid {r['pid']})" if r.get("ok") else f"❌ {r.get('error')}"
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


def handle(text: str, channel_id: int, is_admin: bool = True) -> str:
    """One user message -> one reply. Single LLM call + optional action exec."""
    mi = select_model(False)
    if mi["backend"] == "mock":
        return ("My brain is offline (no Odysseus/Ollama reachable). "
                "Commands still work: try `!help`.")
    h = _hist.setdefault(channel_id, deque(maxlen=8))
    convo = "\n".join(f"{r}: {t}" for r, t in h) or "(new conversation)"
    try:
        raw = complete(_SYS, f"Recent chat:\n{convo}\n\nuser: {text}", mi,
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
        if name in ADMIN and not is_admin:
            out = f"{say}\n🔒 `{name}` is owner-only on this server."
        elif name in SAFE | ADMIN:
            out = f"{say}\n{_exec(name, act.get('args') or {})}".strip()
    h.append(("user", text[:200]))
    h.append(("valentine", out[:200]))
    return out[:1990]
