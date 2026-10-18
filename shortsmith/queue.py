"""A render queue, so one click can produce several videos.

Two things needed this at once. Asking for three videos on a topic means three
renders, and only one can run at a time — a second would fight the first for
the GPU and both would crawl. And "make five overnight" is only useful if the
app keeps going without you.

Variations are the interesting part. Three videos from one brief are worthless
if they come out as three near-identical stories, so each job carries an ANGLE:
a different way into the same subject. The angle is handed to the planner as
part of the brief, which already knows how to develop a direction into a full
story with its own structure.

State lives in memory. A queue that survived restarts would need to survive
half-finished renders too, which is a different and much larger problem.
"""
from __future__ import annotations

import itertools
import threading
import time

from . import control
from .utils import log

# Ways into the same subject. Deliberately about APPROACH rather than content,
# so they work whether the brief is a drama or an explainer.
ANGLES = [
    "open in the middle of the most surprising moment",
    "open quietly and ordinary, and let the wrongness build",
    "tell it from the consequence backwards — start at the aftermath",
    "lead with the detail everyone gets wrong about this",
    "frame it as a question the viewer will want answered",
    "start on the smallest concrete object in the story and widen out",
]

_jobs: list = []
_lock = threading.Lock()
_runner: list = []
_ids = itertools.count(1)


def _new_job(brief: str, seconds, voice: str, channel: str, angle: str,
             mode: str) -> dict:
    return {"id": next(_ids), "brief": brief, "seconds": seconds, "voice": voice,
            "channel": channel, "angle": angle, "mode": mode, "state": "waiting",
            "started": None, "finished": None, "error": None}


def add(brief: str, seconds, voice: str, channel: str, count: int = 1,
        mode: str = "express") -> dict:
    """Queue `count` videos on one brief, each with a different angle."""
    count = max(1, min(10, int(count or 1)))
    made = []
    with _lock:
        for i in range(count):
            # One video gets the brief untouched; the rest get an angle, so a
            # single request still behaves exactly as it always did.
            angle = "" if count == 1 else ANGLES[i % len(ANGLES)]
            job = _new_job(brief, seconds, voice, channel, angle, mode)
            _jobs.append(job)
            made.append(job["id"])
    _ensure_runner()
    log("info", f"queued {count} video(s)")
    return {"ok": True, "queued": made}


def _brief_with_angle(job: dict) -> str:
    if not job.get("angle"):
        return job["brief"]
    base = job["brief"] or "any subject you like"
    return f"{base}\n\nAPPROACH FOR THIS VERSION: {job['angle']}."


def _run_loop() -> None:
    while True:
        with _lock:
            job = next((j for j in _jobs if j["state"] == "waiting"), None)
        if job is None:
            with _lock:
                _runner.clear()
            return
        # Never start on top of a render that is already going.
        while control.render_in_progress():
            time.sleep(4)
        with _lock:
            job["state"] = "running"
            job["started"] = time.time()
        try:
            r = control.run_now(job["channel"], control.RENDER_SLOT, False,
                                voice=job["voice"],
                                topic=_brief_with_angle(job),
                                seconds=job["seconds"],
                                mode=job.get("mode") or "express")
            if not r.get("ok"):
                raise RuntimeError(r.get("error") or "could not start")
            # Wait for this one to finish before taking the next.
            time.sleep(8)
            while control.render_in_progress():
                time.sleep(5)
            with _lock:
                job["state"] = "done"
                job["finished"] = time.time()
        except Exception as exc:  # noqa: BLE001
            log("warn", f"queued job {job['id']} failed: {exc}")
            with _lock:
                job["state"] = "failed"
                job["error"] = str(exc)
                job["finished"] = time.time()


def _ensure_runner() -> None:
    with _lock:
        if _runner:
            return
        _runner.append(True)
    threading.Thread(target=_run_loop, daemon=True).start()


def state() -> dict:
    with _lock:
        jobs = [dict(j) for j in _jobs]
    waiting = sum(1 for j in jobs if j["state"] == "waiting")
    running = sum(1 for j in jobs if j["state"] == "running")
    return {"jobs": jobs[-20:], "waiting": waiting, "running": running,
            "active": bool(waiting or running)}


def cancel(job_id: int | None = None) -> dict:
    """Drop one waiting job, or clear the whole queue."""
    with _lock:
        if job_id is None:
            dropped = [j for j in _jobs if j["state"] == "waiting"]
            for j in dropped:
                j["state"] = "cancelled"
            return {"ok": True, "cancelled": len(dropped)}
        for j in _jobs:
            if j["id"] == job_id and j["state"] == "waiting":
                j["state"] = "cancelled"
                return {"ok": True, "cancelled": 1}
    return {"ok": False, "error": "that job is not waiting"}


def clear_finished() -> dict:
    with _lock:
        before = len(_jobs)
        keep = [j for j in _jobs if j["state"] in ("waiting", "running")]
        _jobs.clear()
        _jobs.extend(keep)
    return {"ok": True, "removed": before - len(_jobs)}
