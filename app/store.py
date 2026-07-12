"""Backend logic shared by both server implementations (FastAPI + stdlib fallback).

No web framework here — just functions over the on-disk state that the pipeline
and scheduler write. Keeping it framework-free is what lets the control app boot
with zero pip installs (stdlib server) OR with FastAPI, from the same logic.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from storyforge.config import list_channels, load_channel
from storyforge.notify import add_subscription, recent_events
from storyforge.providers.cloud import cap_remaining
from storyforge.scheduler import get_assignment, set_assignment
from storyforge.state import approved_dir, pending_dir
from storyforge.utils import (OUTPUT_DIR, ROOT, STATE_DIR, ensure_dir, env, read_json,
                              read_status, utcnow_iso, write_json)

_SIDE_EXT = (".mp4", ".jpg", ".png", ".json", ".report.txt")


# --------------------------------------------------------------------------- #
# Auth token
# --------------------------------------------------------------------------- #
def app_token() -> str:
    """Bearer token every route checks. Auto-generated + persisted if unset."""
    tok = env("APP_TOKEN")
    if tok:
        return tok
    cache = STATE_DIR / "app_token.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8").strip()
    tok = os.urandom(16).hex()
    cache.write_text(tok, encoding="utf-8")
    return tok


def vapid_public_key() -> str:
    return env("VAPID_PUBLIC_KEY", "") or ""


# --------------------------------------------------------------------------- #
# Status / config
# --------------------------------------------------------------------------- #
def config_info() -> dict:
    channels = list_channels() or ["history_mysteries", "gaming_lore"]
    return {
        "channels": [{"key": c, "name": load_channel(c).get("name", c)} for c in channels],
        "lanes": ["night", "day"],
        "vapid_public_key": vapid_public_key(),
        "push_configured": bool(vapid_public_key()),
    }


def status() -> dict:
    return {
        "now": utcnow_iso(),
        "lanes": {"night": read_status("night"), "day": read_status("day")},
        "assignment": get_assignment(),
        "cloud_cap_remaining": cap_remaining(),
        "pending_count": len(list_pending()),
    }


# --------------------------------------------------------------------------- #
# Pending / approved items
# --------------------------------------------------------------------------- #
def _item_from_json(jp: Path) -> dict:
    meta = read_json(jp, {}) or {}
    meta["id"] = jp.stem
    meta["_dir"] = str(jp.parent)
    return meta


def _iter_pending_jsons():
    for ch_dir in OUTPUT_DIR.glob("*/pending"):
        for jp in sorted(ch_dir.glob("*.json"), reverse=True):
            yield jp


def list_pending() -> list[dict]:
    items = []
    for jp in _iter_pending_jsons():
        m = _item_from_json(jp)
        items.append({k: m.get(k) for k in (
            "id", "channel", "channel_name", "lane", "title", "created",
            "runtime_sec", "thumbnail", "video", "mock", "script_source",
            "captions_kind", "published_youtube_id")})
    items.sort(key=lambda x: x.get("created") or "", reverse=True)
    return items


def find_item(item_id: str, where: str = "pending") -> dict | None:
    for ch_dir in OUTPUT_DIR.glob(f"*/{where}"):
        jp = ch_dir / f"{item_id}.json"
        if jp.exists():
            return _item_from_json(jp)
    return None


def get_pending(item_id: str) -> dict | None:
    m = find_item(item_id, "pending")
    if not m:
        return None
    report = Path(m["_dir"]) / f"{item_id}.report.txt"
    m["report_text"] = report.read_text(encoding="utf-8") if report.exists() else ""
    return m


def _sidecars(directory: Path, item_id: str) -> list[Path]:
    return [p for p in directory.glob(f"{item_id}.*")]


def approve(item_id: str) -> dict:
    m = find_item(item_id, "pending")
    if not m:
        return {"ok": False, "error": "not found"}
    channel = m.get("channel")
    src_dir = Path(m["_dir"])
    dst_dir = approved_dir(channel)
    moved = []
    for p in _sidecars(src_dir, item_id):
        dst = dst_dir / p.name
        p.replace(dst)
        moved.append(dst.name)
    jp = dst_dir / f"{item_id}.json"
    if jp.exists():
        meta = read_json(jp, {})
        meta["status"] = "approved"
        meta["approved_at"] = utcnow_iso()
        write_json(jp, meta)
    return {"ok": True, "moved": moved, "channel": channel}


def reject(item_id: str) -> dict:
    m = find_item(item_id, "pending")
    if not m:
        return {"ok": False, "error": "not found"}
    src_dir = Path(m["_dir"])
    deleted = []
    for p in _sidecars(src_dir, item_id):
        try:
            p.unlink()
            deleted.append(p.name)
        except Exception:
            pass
    return {"ok": True, "deleted": deleted}


def history(limit: int = 50) -> list[dict]:
    items = []
    for ch_dir in OUTPUT_DIR.glob("*/approved"):
        for jp in sorted(ch_dir.glob("*.json"), reverse=True):
            m = _item_from_json(jp)
            items.append({k: m.get(k) for k in (
                "id", "channel", "channel_name", "lane", "title", "created",
                "approved_at", "runtime_sec", "thumbnail", "video",
                "published_youtube_id")})
    items.sort(key=lambda x: x.get("approved_at") or x.get("created") or "", reverse=True)
    return items[:limit]


def media_path(item_id: str, kind: str) -> Path | None:
    """Resolve a media file (video/thumb) for an item in pending or approved."""
    for where in ("pending", "approved"):
        m = find_item(item_id, where)
        if not m:
            continue
        d = Path(m["_dir"])
        name = m.get("video") if kind == "video" else m.get("thumbnail")
        if name and (d / name).exists():
            return d / name
    return None


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def run_now(channel: str, lane: str, mock: bool = False) -> dict:
    if channel not in (list_channels() or []):
        return {"ok": False, "error": f"unknown channel {channel}"}
    logdir = ensure_dir(STATE_DIR / "runs")
    logfile = logdir / f"{channel}_{lane}_{utcnow_iso().replace(':', '').replace('-', '')}.log"
    cmd = [sys.executable, str(ROOT / "run.py"), "--channel", channel, "--lane", lane]
    if mock:
        cmd.append("--mock")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS|NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    fh = open(logfile, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, **kwargs)
    return {"ok": True, "pid": proc.pid, "log": str(logfile), "cmd": " ".join(cmd)}


def get_assignment_public() -> dict:
    a = get_assignment()
    return {"date": a.get("date"), "night": a.get("night"), "day": a.get("day"),
            "last": a.get("last")}


def set_lanes(night: str, day: str) -> dict:
    set_assignment(night, day)
    return {"ok": True, "assignment": get_assignment()}


def subscribe_push(subscription: dict) -> dict:
    add_subscription(subscription)
    return {"ok": True}


def events(limit: int = 100) -> list[dict]:
    return list(reversed(recent_events(limit)))
