"""Interface-agnostic control logic (used by the Discord bot + CLI).

Pure functions over the on-disk state the pipeline/scheduler write.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from .config import list_channels, load_channel
from .llm import select_model
from .notify import recent_events
from .state import approved_dir
from .utils import (CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, OUTPUT_DIR, ROOT,
                    STATE_DIR, ensure_dir, env, read_json, read_status, utcnow_iso,
                    write_json, write_status)

# The single, neutral slot every on-demand render writes its status into.
# (There is no "night"/"day" any more — Shortsmith is on-demand only.)
RENDER_SLOT = "render"
_PIDFILE = STATE_DIR / "render.pid"


def config_info() -> dict:
    channels = list_channels() or ["gossip_stories"]
    return {
        "channels": [{"key": c, "name": load_channel(c).get("name", c)} for c in channels],
        "auto_publish": (env("AUTO_PUBLISH", "off") or "off").lower(),
    }


def status() -> dict:
    return {
        "now": utcnow_iso(),
        "render": read_status(RENDER_SLOT),
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


def get_item(item_id: str) -> dict | None:
    """An item from pending/ or approved/, wherever it lives."""
    return find_item(item_id, "pending") or find_item(item_id, "approved")


def save_metadata(item_id: str, meta: dict) -> dict:
    """Attach metadata to one video's own record."""
    for where in ("pending", "approved"):
        m = find_item(item_id, where)
        if not m:
            continue
        jp = Path(m["_dir"]) / f"{item_id}.json"
        data = read_json(jp, {}) or {}
        data["metadata"] = meta
        if meta.get("title"):
            data["title"] = meta["title"]
        if meta.get("hashtags"):
            data["hashtags"] = meta["hashtags"]
        if meta.get("tags"):
            data["tags"] = meta["tags"]
        if meta.get("description"):
            data["description"] = meta["description"]
        write_json(jp, data)
        return {"ok": True}
    return {"ok": False, "error": "not found"}


def delete_item(item_id: str, where: str = "pending") -> dict:
    """Delete one item's files from pending/ or approved/.

    reject() only looks in pending/, so an approved video could never be
    removed from inside the app — the Library needs this.
    """
    m = find_item(item_id, where)
    if not m:
        return {"ok": False, "error": "not found"}
    deleted = []
    for p in Path(m["_dir"]).glob(f"{item_id}.*"):
        try:
            p.unlink()
            deleted.append(p.name)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "deleted": deleted}


def history(limit: int = 20) -> list[dict]:
    items = []
    for ch_dir in OUTPUT_DIR.glob("*/approved"):
        for jp in sorted(ch_dir.glob("*.json"), reverse=True):
            items.append(_item_from_json(jp))
    items.sort(key=lambda x: x.get("approved_at") or x.get("created") or "", reverse=True)
    return items[:limit]


DISCORD_LIMIT = int(9.5 * 1024 * 1024)  # safe under a non-boosted server's ~10MB cap


def discord_preview(item_id: str, max_bytes: int = DISCORD_LIMIT) -> Path | None:
    """A video file guaranteed to fit Discord's upload cap.

    Returns the master if already small; otherwise builds (and caches) a
    progressively smaller preview until one fits. The full-quality master is never
    touched — previews are only for reviewing in chat."""
    from .utils import run_ffmpeg
    v = media_path(item_id, "video")
    if not v:
        return None
    if v.stat().st_size <= max_bytes:
        return v
    prev = v.with_name(f"{item_id}.preview.mp4")
    if prev.exists() and 0 < prev.stat().st_size <= max_bytes:
        return prev
    # step down resolution/quality until it fits (540p is fine for review)
    for height, crf in ((960, 30), (720, 32), (540, 34), (480, 36)):
        ok, _ = run_ffmpeg(["-i", str(v), "-vf", f"scale=-2:{height}",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                            "-c:a", "aac", "-b:a", "80k", "-movflags", "+faststart",
                            str(prev)], cwd=v.parent, timeout=600)
        if ok and prev.exists() and 0 < prev.stat().st_size <= max_bytes:
            return prev
    return None  # caller falls back to thumbnail + path


def media_path(item_id: str, kind: str) -> Path | None:
    for where in ("pending", "approved"):
        m = find_item(item_id, where)
        if m:
            name = m.get("video") if kind == "video" else m.get("thumbnail")
            if name and (Path(m["_dir"]) / name).exists():
                return Path(m["_dir"]) / name
    return None


# ---- actions ---------------------------------------------------------------- #
def brain_status() -> dict:
    """Which model is writing, and why that one."""
    b = select_model(False)
    return {"backend": b["backend"], "model": b["model"], "reason": b["reason"]}


def _spawn(cmd: list[str], tag: str) -> dict:
    logdir = ensure_dir(STATE_DIR / "runs")
    logfile = logdir / f"{tag}_{utcnow_iso().replace(':', '').replace('-', '')}.log"
    # CREATE_NO_WINDOW (not DETACHED_PROCESS): the render keeps a hidden console
    # that its ffmpeg children inherit, so nothing ever flashes on screen.
    # DETACHED_PROCESS leaves children console-less, making each one pop its own.
    kwargs = ({"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})
    fh = open(logfile, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **kwargs)
    return {"ok": True, "pid": proc.pid, "log": str(logfile), "cmd": " ".join(cmd)}


def run_now(channel: str, lane: str = RENDER_SLOT, mock: bool = False,
            voice: str | None = None, topic: str = "",
            seconds: float | None = None, mode: str = "express") -> dict:
    if channel not in (list_channels() or []):
        return {"ok": False, "error": f"unknown channel {channel}"}
    cmd = [sys.executable, str(ROOT / "run.py"), "--channel", channel, "--lane", lane]
    if mock:
        cmd.append("--mock")
    if voice in ("adam", "squeaky"):
        cmd += ["--voice", voice]
    if topic:
        cmd += ["--topic", topic[:600]]
    if seconds:
        cmd += ["--seconds", str(float(seconds))]
    if mode in ("guided", "express", "auto"):
        cmd += ["--mode", mode]
    res = _spawn(cmd, f"{channel}_{lane}")
    if lane == RENDER_SLOT and res.get("ok"):
        try:
            _PIDFILE.write_text(str(res["pid"]))
        except Exception:
            pass
    return res


def render_in_progress() -> bool:
    """True only while a render is actually between script and herald (not done)."""
    st = read_status(RENDER_SLOT) or {}
    stage = str(st.get("stage") or "").lower()
    if stage in ("", "idle", "failed", "cancelled"):
        return False
    return not (st.get("pct") == 100.0 or "pending/" in str(st.get("message") or ""))


def cancel_render() -> dict:
    """Stop the in-progress on-demand render (and its ffmpeg children), if any."""
    # A run paused at the script gate is waiting on a file, not on the CPU.
    # Removing the request releases it even before the kill lands.
    try:
        from .gate import drop_all
        drop_all()
    except Exception:  # noqa: BLE001
        pass
    if not render_in_progress():
        try:
            _PIDFILE.unlink()
        except Exception:
            pass
        return {"ok": True, "killed": False, "reason": "nothing was rendering"}
    pid = None
    try:
        pid = int((_PIDFILE.read_text().strip() or "0")) or None
    except Exception:
        pid = None
    killed = False
    if pid:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            killed = True
        except Exception:
            killed = False
    try:
        write_status(RENDER_SLOT, {"stage": "cancelled", "message": "cancelled by user",
                                   "pct": None})
    except Exception:
        pass
    try:
        _PIDFILE.unlink()
    except Exception:
        pass
    return {"ok": True, "killed": killed, "pid": pid}


def events(limit: int = 20) -> list[dict]:
    return list(reversed(recent_events(limit)))
