"""Shared node helpers: live progress + fallback/error bookkeeping.

Every node calls progress() so the PWA can show which stage each lane is in, and
add_fallback()/add_error() so degradations and failures land in the bottleneck
feed and (for yellow/red) get pushed to the phone.
"""
from __future__ import annotations

from ..notify import notify
from ..utils import write_status

STAGES = ["script", "voice", "canvas", "cutter", "herald"]


def progress(state: dict, stage: str, message: str, pct: float | None = None,
             severity: str = "info", extra: dict | None = None) -> None:
    state["stage"] = stage
    data = {
        "stage": stage,
        "message": message,
        "pct": pct,
        "run_id": state.get("run_id"),
        "channel": state.get("channel"),
        "lane": state.get("lane"),
        "mock": state.get("mock"),
        "title": (state.get("script") or {}).get("title"),
    }
    if extra:
        data.update(extra)
    write_status(state.get("lane", "?"), data)
    notify(severity, state.get("lane", "?"), state.get("channel", "?"),
           stage, message, details=extra, push=(severity in ("red", "yellow", "green")))


def add_fallback(state: dict, stage: str, note: str) -> None:
    state.setdefault("fallbacks", []).append(note)
    notify("yellow", state.get("lane", "?"), state.get("channel", "?"), stage, note)


def add_error(state: dict, stage: str, note: str) -> None:
    state.setdefault("errors", []).append(note)
    notify("red", state.get("lane", "?"), state.get("channel", "?"), stage, note)


def mark_done(state: dict, stage: str, message: str, extra: dict | None = None) -> None:
    progress(state, stage, message, pct=100.0, severity="green", extra=extra)
