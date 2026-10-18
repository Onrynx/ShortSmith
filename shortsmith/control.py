"""Interface-agnostic control logic (used by the Discord bot + CLI).

Pure functions over the on-disk state the pipeline/scheduler write.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import sys
from pathlib import Path

from .config import list_channels, load_channel
from .llm import select_model
from .notify import recent_events
from .safety import inside, safe_id
from .state import approved_dir
from .utils import (CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, OUTPUT_DIR, ROOT,
                    STATE_DIR, ensure_dir, env, log, read_json, read_status,
                    utcnow_iso, write_json, write_status)

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
    # The id arrives from an HTTP path and is about to become a filename.
    # "../../x" is a valid Python string and Path.exists() walks it happily,
    # so it is refused here rather than resolved later.
    ident = safe_id(item_id)
    if ident is None or where not in ("pending", "approved"):
        return None
    for ch_dir in OUTPUT_DIR.glob(f"*/{where}"):
        jp = ch_dir / f"{ident}.json"
        if jp.exists() and inside(jp, ch_dir):
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
        # find_item already refused anything unsafe; rebuild from the checked
        # form rather than the raw argument so the two can never drift.
        jp = Path(m["_dir"]) / f"{m['id']}.json"
        if not inside(jp, m["_dir"]):
            return {"ok": False, "error": "not found"}
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


def _item_files(m: dict, item_id: str) -> list:
    """Every file belonging to one item, and nothing else.

    glob(f"{item_id}.*") would treat a bracket in an id as a character class;
    matching on the stem keeps the selection literal.
    """
    folder = Path(m["_dir"])
    return [p for p in folder.iterdir()
            if p.is_file() and p.name.startswith(item_id + ".")
            and inside(p, folder)]


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


def media_path(item_id: str, kind: str) -> Path | None:
    for where in ("pending", "approved"):
        m = find_item(item_id, where)
        if not m:
            continue
        name = m.get("video") if kind == "video" else m.get("thumbnail")
        # The filename comes out of a JSON record on disk. The app writes
        # those, but a record is still a file anyone could edit, and whatever
        # this returns gets streamed to the browser — so it has to be a plain
        # name that lands inside the item's own folder.
        if not safe_id(name):
            continue
        candidate = Path(m["_dir"]) / str(name)
        if candidate.exists() and inside(candidate, m["_dir"]):
            return candidate
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
    # The child inherits the handle, so the parent closes its own copy rather
    # than holding one open per render for the lifetime of the app.
    with open(logfile, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
    # The command line carries the user's brief and absolute paths; it belongs
    # in the log, not in an HTTP response.
    log("info", f"started {logfile.name} (pid {proc.pid})")
    return {"ok": True, "pid": proc.pid, "log": logfile.name}


# Deciding "is a render running?" and starting one has to be a single step.
# render_in_progress() reads a status file the CHILD writes, so between the
# check and the child's first write there are several seconds in which the
# answer is still "no" — long enough for two clicks, let alone two threads, to
# both get through and fight over the GPU.
_launch_lock = threading.Lock()
_launching = {"at": 0.0}
_LAUNCH_GRACE = 25.0        # seconds a spawn is treated as in-progress


def _recently_launched() -> bool:
    return (time.time() - _launching["at"]) < _LAUNCH_GRACE


def run_now(channel: str, lane: str = RENDER_SLOT, mock: bool = False,
            voice: str | None = None, topic: str = "",
            seconds: float | None = None, mode: str = "express",
            run_id: str = "") -> dict:
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
    if run_id:
        cmd += ["--run-id", run_id]
    if lane == RENDER_SLOT:
        with _launch_lock:
            if render_in_progress() or _recently_launched():
                return {"ok": False, "error": "a render is already running"}
            _launching["at"] = time.time()
            res = _spawn(cmd, f"{channel}_{lane}")
    else:
        res = _spawn(cmd, f"{channel}_{lane}")
    if lane == RENDER_SLOT and res.get("ok"):
        try:
            _PIDFILE.write_text(str(res["pid"]))
        except Exception:
            pass
    return res


# --------------------------------------------------------------------------- #
# Picking up a render that died
# --------------------------------------------------------------------------- #
# Every stage writes a checkpoint into the run's working directory, and a run
# that finishes has that directory deleted. So any checkpoint still on disk is,
# by definition, a render that stopped early — and the stages it already
# finished are the expensive ones. Starting over would re-write the script and
# re-record every line of narration for nothing.
_STAGE_WORDS = {
    "script": "the story", "approve": "your approval", "voice": "the narration",
    "canvas": "the visuals", "cutter": "the edit", "herald": "the finishing",
}
_ALL_STAGES = ["script", "approve", "voice", "canvas", "cutter", "herald", "file"]


def _checkpoints() -> list[dict]:
    """Every unfinished run on disk, newest first."""
    found = []
    for ck in OUTPUT_DIR.glob("*/_work/*/checkpoint.json"):
        data = read_json(ck, None)
        if not isinstance(data, dict):
            continue
        st = data.get("state") or {}
        done = [s for s in _ALL_STAGES if s in (data.get("completed") or [])]
        if not done or not st.get("run_id"):
            continue
        try:
            when = ck.stat().st_mtime
        except OSError:
            continue
        nxt = next((x for x in _ALL_STAGES if x not in done), None)
        found.append({
            "run_id": st["run_id"],
            "channel": st.get("channel") or ck.parents[2].name,
            "title": (st.get("script") or {}).get("title") or st.get("topic") or "Untitled",
            "topic": st.get("topic") or "",
            "voice": st.get("voice_style") or "adam",
            "seconds": st.get("requested_seconds"),
            "mode": st.get("mode") or "express",
            "done": done,
            "done_words": [_STAGE_WORDS.get(d, d) for d in done
                           if d not in ("approve", "file")],
            "next": nxt,
            "next_word": _STAGE_WORDS.get(nxt, nxt or "the rest"),
            "when": when,
            "dir": str(ck.parent),
        })
    return sorted(found, key=lambda r: r["when"], reverse=True)


def resumable() -> dict | None:
    """The newest render that could be picked up, if any."""
    if render_in_progress():
        return None
    found = _checkpoints()
    return found[0] if found else None


def resume_render(run_id: str = "") -> dict:
    """Start a stopped render again. Finished stages are skipped, not redone."""
    if render_in_progress():
        return {"ok": False, "error": "a render is already running"}
    found = _checkpoints()
    item = next((r for r in found if r["run_id"] == run_id), None) if run_id else            (found[0] if found else None)
    if not item:
        return {"ok": False, "error": "there is nothing to carry on from"}
    # The saved state carries the mode and the script; the topic has to be
    # handed back in because run_lite deliberately re-reads it from the new
    # invocation (so a resume can never silently change what was asked for).
    return run_now(item["channel"], RENDER_SLOT, False, voice=item["voice"],
                   topic=item["topic"], seconds=item["seconds"],
                   mode=item["mode"], run_id=item["run_id"])


def discard_resumable(run_id: str = "") -> dict:
    """Throw away a stopped render's working files."""
    import shutil
    found = _checkpoints()
    targets = [r for r in found if not run_id or r["run_id"] == run_id]
    if not targets:
        return {"ok": False, "error": "there is nothing to discard"}
    freed = 0
    for t in targets:
        d = Path(t["dir"])
        if d.name != t["run_id"] or "_work" not in d.parts:
            continue        # refuse anything that is not scratch
        try:
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
    return {"ok": True, "discarded": len(targets), "freed_mb": round(freed / 1024 ** 2)}


def render_in_progress() -> bool:
    """True only while a render is actually between script and herald (not done)."""
    st = read_status(RENDER_SLOT) or {}
    stage = str(st.get("stage") or "").lower()
    if stage in ("", "idle", "failed", "cancelled"):
        return False
    return not (st.get("pct") == 100.0 or "pending/" in str(st.get("message") or ""))


def _is_our_render(pid: int) -> bool:
    """Is this pid still the render we started, or has the OS reused it?

    render.pid outlives a crash, and the operating system hands that number to
    somebody else soon enough. Without this check, pressing Cancel after a
    failed render can terminate whatever inherited the number — and on Windows
    `taskkill /T` takes its children with it. boot.py already guards its
    Ollama pid the same way; this is the other half.
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        # Without psutil we cannot tell, and killing the wrong process is
        # worse than leaving a dead render's pid file behind.
        log("warn", "psutil is not installed; refusing to kill by pid alone")
        return False
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        name = (proc.name() or "").lower()
        if "python" not in name:
            return False
        cmdline = " ".join(proc.cmdline() or []).lower()
    except Exception:  # noqa: BLE001  (gone, or not ours to inspect)
        return False
    # Our renders are always `python <ROOT>/run.py ...`.
    return "run.py" in cmdline and str(ROOT).lower() in cmdline


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
    if pid and _is_our_render(pid):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            killed = True
        except Exception:
            killed = False
    elif pid:
        log("warn", f"pid {pid} is not our render any more; not killing it")
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
