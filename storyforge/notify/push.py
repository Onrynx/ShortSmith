"""Notifications: bottleneck feed + Discord webhook (zero extra dependencies).

`notify()` is wrapped around every stage. It ALWAYS:
  1. appends the event to the feed (STATE/events.jsonl) — nothing is ever lost;
  2. logs it with a severity emoji;
  3. best-effort POSTs red/yellow/green events to DISCORD_WEBHOOK_URL (stdlib
     urllib; no bot needed for outbound). The Discord bot handles commands.

Severities: 'red' 🔴 lane failed · 'yellow' 🟡 degraded-but-shipped · 'green' 🟢 done.
"""
from __future__ import annotations

from typing import Any, Optional

from ..utils import (STATE_DIR, append_jsonl, env, http_post_json, log,
                     read_jsonl, utcnow_iso)

EVENTS_FILE = STATE_DIR / "events.jsonl"

ICON = {"red": "🔴", "yellow": "🟡", "green": "🟢", "info": "•"}
_LOGLEVEL = {"red": "error", "yellow": "warn", "green": "ok", "info": "info"}
_COLOR = {"red": 0xF85149, "yellow": 0xE3B341, "green": 0x3FB950}


def _send_discord(severity: str, lane: str, channel: str, stage: str,
                  message: str) -> None:
    url = env("DISCORD_WEBHOOK_URL")
    if not url:
        return
    payload = {
        "username": "StoryForge",
        "embeds": [{
            "title": f"{ICON[severity]} {channel} · {lane} · {stage}",
            "description": message[:2000],
            "color": _COLOR.get(severity, 0x8B949E),
        }],
    }
    try:
        http_post_json(url, payload, timeout=10)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"discord webhook failed: {exc}")


def notify(severity: str, lane: str, channel: str, stage: str, message: str,
           details: Optional[Any] = None, push: bool = True) -> dict:
    severity = severity if severity in ICON else "info"
    event = {"ts": utcnow_iso(), "severity": severity, "lane": lane,
             "channel": channel, "stage": stage, "message": message, "details": details}
    try:
        append_jsonl(EVENTS_FILE, event)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not append event: {exc}")
    log(_LOGLEVEL.get(severity, "info"), f"[{lane}/{channel}] {stage}: {message}")
    if push and severity in ("red", "yellow", "green"):
        _send_discord(severity, lane, channel, stage, message)
    return event


def recent_events(limit: int = 100) -> list[dict]:
    return read_jsonl(EVENTS_FILE, limit=limit)
