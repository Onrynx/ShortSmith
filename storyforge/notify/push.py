"""Notifications: bottleneck feed + self-hosted Web Push (VAPID, no 3rd-party service).

`notify()` is the single entry point wrapped around every stage. It ALWAYS:
  1. appends the event to the bottleneck feed (STATE/events.jsonl) — the PWA reads
     this even when push isn't configured, so nothing is ever lost;
  2. logs it with a severity emoji;
  3. best-effort sends a Web Push to every stored subscription (needs pywebpush +
     VAPID keys in .env; degrades to log-only otherwise).

Severities: 'red' 🔴 lane failed · 'yellow' 🟡 degraded-but-shipped · 'green' 🟢 done.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..utils import (STATE_DIR, append_jsonl, env, log, read_json, read_jsonl,
                     utcnow_iso, write_json)

EVENTS_FILE = STATE_DIR / "events.jsonl"
SUBS_FILE = STATE_DIR / "subscriptions.json"

ICON = {"red": "🔴", "yellow": "🟡", "green": "🟢", "info": "•"}
_LOGLEVEL = {"red": "error", "yellow": "warn", "green": "ok", "info": "info"}


# --------------------------------------------------------------------------- #
# Subscription store (managed by the PWA subscribe endpoint)
# --------------------------------------------------------------------------- #
def load_subscriptions() -> list[dict]:
    return read_json(SUBS_FILE, []) or []


def add_subscription(sub: dict) -> None:
    subs = load_subscriptions()
    endpoint = sub.get("endpoint")
    if endpoint and not any(s.get("endpoint") == endpoint for s in subs):
        subs.append(sub)
        write_json(SUBS_FILE, subs)
        log("info", "stored a new Web Push subscription")


def remove_subscription(endpoint: str) -> None:
    subs = [s for s in load_subscriptions() if s.get("endpoint") != endpoint]
    write_json(SUBS_FILE, subs)


# --------------------------------------------------------------------------- #
# Web Push
# --------------------------------------------------------------------------- #
def _send_webpush(payload: dict) -> None:
    subs = load_subscriptions()
    if not subs:
        return
    priv = env("VAPID_PRIVATE_KEY")
    subject = env("VAPID_SUBJECT", "mailto:admin@storyforge.local")
    if not priv:
        return  # not configured yet — feed still has the event
    # VAPID_PRIVATE_KEY may be a path to the PEM (from gen_vapid.py) or the value.
    try:
        import os
        if os.path.isfile(priv):
            priv = open(priv, "r", encoding="utf-8").read()
    except Exception:
        pass
    try:
        from pywebpush import webpush, WebPushException  # type: ignore
    except Exception:
        log("info", "pywebpush not installed — notification stored to feed only")
        return
    dead: list[str] = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=priv,
                vapid_claims={"sub": subject},
                timeout=10,
            )
        except WebPushException as exc:  # type: ignore
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                dead.append(sub.get("endpoint", ""))
            log("warn", f"web push failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            log("warn", f"web push error: {exc}")
    for ep in dead:
        remove_subscription(ep)


# --------------------------------------------------------------------------- #
# The wrap-every-stage entry point
# --------------------------------------------------------------------------- #
def notify(severity: str, lane: str, channel: str, stage: str, message: str,
           details: Optional[Any] = None, push: bool = True) -> dict:
    severity = severity if severity in ICON else "info"
    event = {
        "ts": utcnow_iso(),
        "severity": severity,
        "lane": lane,
        "channel": channel,
        "stage": stage,
        "message": message,
        "details": details,
    }
    try:
        append_jsonl(EVENTS_FILE, event)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not append event: {exc}")

    # log() already prefixes a severity icon; don't double it up here.
    log(_LOGLEVEL.get(severity, "info"), f"[{lane}/{channel}] {stage}: {message}")

    if push and severity in ("red", "yellow", "green"):
        try:
            _send_webpush({
                "title": f"{ICON[severity]} StoryForge · {channel} ({lane})",
                "body": f"{stage}: {message}",
                "tag": f"{lane}-{stage}",
                "data": {"lane": lane, "channel": channel, "stage": stage},
            })
        except Exception as exc:  # noqa: BLE001
            log("warn", f"push dispatch error: {exc}")
    return event


def recent_events(limit: int = 100) -> list[dict]:
    return read_jsonl(EVENTS_FILE, limit=limit)
