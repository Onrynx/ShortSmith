"""Interface-agnostic control logic (used by the Discord bot + CLI).

Pure functions over the on-disk state the pipeline/scheduler write.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .config import list_channels, load_channel
from .llm import (_odysseus_models, odysseus_api_base, odysseus_base_url,
                  odysseus_reachable, odysseus_ui_reachable, select_model)
from .notify import recent_events
from .providers.cloud import cap_remaining
from .scheduler import get_assignment, set_assignment
from .state import approved_dir
from .utils import (OUTPUT_DIR, ROOT, STATE_DIR, ensure_dir, env, read_json,
                    read_status, utcnow_iso, write_json)


def config_info() -> dict:
    channels = list_channels() or ["history_mysteries", "gaming_lore"]
    return {
        "channels": [{"key": c, "name": load_channel(c).get("name", c)} for c in channels],
        "lanes": ["night", "day"],
        "auto_publish": (env("AUTO_PUBLISH", "off") or "off").lower(),
        "odysseus_url": odysseus_base_url(),
    }


def status() -> dict:
    return {
        "now": utcnow_iso(),
        "lanes": {"night": read_status("night"), "day": read_status("day")},
        "assignment": get_assignment(),
        "cloud_cap_remaining": cap_remaining(),
        "pending_count": len(list_pending()),
    }


# ---- pending / approved ---------------------------------------------------- #
def _item_from_json(jp: Path) -> dict:
    meta = read_json(jp, {}) or {}
    meta["id"] = jp.stem
    meta["_dir"] = str(jp.parent)
    return meta


def list_pending() -> list[dict]:
    items = []
    for ch_dir in OUTPUT_DIR.glob("*/pending"):
        for jp in sorted(ch_dir.glob("*.json"), reverse=True):
            items.append(_item_from_json(jp))
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


def approve(item_id: str) -> dict:
    m = find_item(item_id, "pending")
    if not m:
        return {"ok": False, "error": "not found"}
    dst_dir = approved_dir(m.get("channel"))
    moved = []
    for p in Path(m["_dir"]).glob(f"{item_id}.*"):
        dst = dst_dir / p.name
        p.replace(dst)
        moved.append(dst.name)
    jp = dst_dir / f"{item_id}.json"
    if jp.exists():
        meta = read_json(jp, {})
        meta["status"] = "approved"
        meta["approved_at"] = utcnow_iso()
        write_json(jp, meta)
    return {"ok": True, "moved": moved, "channel": m.get("channel")}


def reject(item_id: str) -> dict:
    m = find_item(item_id, "pending")
    if not m:
        return {"ok": False, "error": "not found"}
    deleted = []
    for p in Path(m["_dir"]).glob(f"{item_id}.*"):
        try:
            p.unlink()
            deleted.append(p.name)
        except Exception:
            pass
    return {"ok": True, "deleted": deleted}


def history(limit: int = 20) -> list[dict]:
    items = []
    for ch_dir in OUTPUT_DIR.glob("*/approved"):
        for jp in sorted(ch_dir.glob("*.json"), reverse=True):
            items.append(_item_from_json(jp))
    items.sort(key=lambda x: x.get("approved_at") or x.get("created") or "", reverse=True)
    return items[:limit]


def media_path(item_id: str, kind: str) -> Path | None:
    for where in ("pending", "approved"):
        m = find_item(item_id, where)
        if m:
            name = m.get("video") if kind == "video" else m.get("thumbnail")
            if name and (Path(m["_dir"]) / name).exists():
                return Path(m["_dir"]) / name
    return None


# ---- actions ---------------------------------------------------------------- #
def odysseus_status() -> dict:
    llm_up = odysseus_reachable()
    sel = select_model(False)
    return {
        "url": odysseus_base_url(),
        "ui_up": odysseus_ui_reachable(),
        "llm_v1_up": llm_up,
        "models": _odysseus_models(odysseus_api_base()) if llm_up else [],
        "storyforge_brain": {"backend": sel.get("backend"), "model": sel.get("model"),
                             "reason": sel.get("reason")},
    }


def _spawn(cmd: list[str], tag: str) -> dict:
    logdir = ensure_dir(STATE_DIR / "runs")
    logfile = logdir / f"{tag}_{utcnow_iso().replace(':', '').replace('-', '')}.log"
    kwargs = ({"creationflags": 0x00000208} if os.name == "nt"
              else {"start_new_session": True})
    fh = open(logfile, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, **kwargs)
    return {"ok": True, "pid": proc.pid, "log": str(logfile), "cmd": " ".join(cmd)}


def run_now(channel: str, lane: str, mock: bool = False) -> dict:
    if channel not in (list_channels() or []):
        return {"ok": False, "error": f"unknown channel {channel}"}
    cmd = [sys.executable, str(ROOT / "run.py"), "--channel", channel, "--lane", lane]
    if mock:
        cmd.append("--mock")
    return _spawn(cmd, f"{channel}_{lane}")


def run_scheduler_daily(mock: bool = False) -> dict:
    cmd = [sys.executable, "-m", "storyforge.scheduler", "daily", "--no-wait"]
    if mock:
        cmd.append("--mock")
    return _spawn(cmd, "daily")


def set_lanes(night: str, day: str) -> dict:
    set_assignment(night, day)
    return {"ok": True, "assignment": get_assignment()}


def events(limit: int = 20) -> list[dict]:
    return list(reversed(recent_events(limit)))
