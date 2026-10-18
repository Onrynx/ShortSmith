"""The script gate — read the story before thirteen minutes are spent on it.

The Make screen has always offered Guided, Express and Full auto, and until now
all three did exactly the same thing: write the story, then render it whatever
it said. That is the wrong place to find out the AI misread your brief. The
script takes about forty seconds; everything after it — voice, backgrounds,
cutting, captions — takes a quarter of an hour, and all of it is wasted if the
first paragraph is wrong.

So the pipeline stops between writing and rendering:

    Guided     stop, show the script, wait for a decision
    Express    never stop; the finished video still waits in Review
    Full auto  never stop, and file the video itself

A stopped run is a real pause, not a cancelled one. The render process stays
alive with its plan, its timing calibration and its model loaded, polling one
small file for an answer, so approving costs nothing and resuming is instant.
Three answers are possible: go, rewrite it with this note, or throw it away.

A rewrite re-runs the writing stage with the note appended to the brief, which
is the same mechanism the variation angles use. The run keeps its id, so a
story can be sent back as many times as it takes.

Nothing here can strand a render. Every wait has a deadline, a missing or
unreadable answer is treated as nobody having answered, and deleting the
request from outside — which is what cancelling does — releases the run.
"""
from __future__ import annotations

import time
from pathlib import Path

from .safety import inside, safe_id
from .utils import STATE_DIR, ensure_dir, log, read_json, write_json

GATE_DIR = ensure_dir(STATE_DIR / "gate")
# A pause is a person-sized wait, not a machine-sized one — long enough to make
# a coffee or come back tomorrow, short enough that a forgotten run eventually
# lets go of the GPU instead of holding it forever.
MAX_WAIT = 12 * 3600
MODES = ("guided", "express", "auto")


def _req_path(run_id: str) -> Path | None:
    """The request file for a run, or None if the id cannot be trusted.

    This id arrives in a POST body and from JSON on disk, and it is about to
    become a filename that gets WRITTEN. Callers treat None as "no such run",
    which is the right answer for an id that names nothing reachable.
    """
    ident = safe_id(run_id)
    if ident is None:
        return None
    p = GATE_DIR / f"{ident}.json"
    return p if inside(p, GATE_DIR) else None


def _ans_path(run_id: str) -> Path | None:
    ident = safe_id(run_id)
    if ident is None:
        return None
    p = GATE_DIR / f"{ident}.answer.json"
    return p if inside(p, GATE_DIR) else None


def _clear(run_id: str) -> None:
    for p in (_req_path(run_id), _ans_path(run_id)):
        if p is None:
            continue
        try:
            p.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# What the app sees
# --------------------------------------------------------------------------- #
def waiting() -> dict | None:
    """The script currently waiting for a decision, if there is one."""
    best, newest = None, -1.0
    for p in GATE_DIR.glob("*.json"):
        if p.name.endswith(".answer.json"):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime > newest:
            data = read_json(p, None)
            if isinstance(data, dict) and data.get("run_id"):
                best, newest = data, mtime
    if not best:
        return None
    # A request nobody can answer any more is noise on the screen.
    if time.time() - newest > MAX_WAIT:
        _clear(best["run_id"])
        return None
    return best


def decide(run_id: str, action: str, note: str = "") -> dict:
    """Answer a waiting script. Written by the app, read by the render."""
    action = str(action or "").lower()
    if action not in ("go", "rewrite", "drop"):
        return {"ok": False, "error": "unknown action"}
    req, ans = _req_path(run_id), _ans_path(run_id)
    if req is None or ans is None or not req.exists():
        return {"ok": False, "error": "that script is no longer waiting"}
    if action == "rewrite" and not str(note or "").strip():
        return {"ok": False, "error": "a rewrite needs a note saying what to change"}
    write_json(ans, {"action": action, "note": str(note or "")[:1200],
                     "at": time.time()})
    return {"ok": True, "action": action}


def drop_all() -> int:
    """Forget every waiting request. Cancelling a render calls this."""
    n = 0
    for p in GATE_DIR.glob("*.json"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


# --------------------------------------------------------------------------- #
# What the render does
# --------------------------------------------------------------------------- #
def estimate(state: dict) -> tuple[float, float]:
    """(estimated seconds, requested seconds) for the script as it stands."""
    script = state.get("script") or {}
    targets = state.get("targets") or {}
    scenes = script.get("scenes") or []
    target = float(targets.get("target_seconds") or state.get("requested_seconds") or 0)
    try:
        from .timing import estimate_seconds
        est = float(estimate_seconds(scenes, targets.get("wpm"),
                                     targets.get("calibration")))
    except Exception:  # noqa: BLE001
        # Better a rough number than none: the gate only shows it.
        words = sum(len(str(s.get("narration", "")).split()) for s in scenes)
        est = words / (190.0 / 60.0)
    return est, target


def _summary(state: dict) -> dict:
    script = state.get("script") or {}
    plan = state.get("plan") or {}
    scenes = script.get("scenes") or []
    est, target = estimate(state)
    return {
        "run_id": state.get("run_id"),
        "channel": state.get("channel"),
        "mode": (state.get("mode") or "express").lower(),
        "title": script.get("title") or "Untitled",
        "logline": plan.get("logline") or "",
        "structure": plan.get("structure") or "",
        "kind": plan.get("brief_kind") or "",
        "brief": state.get("gate_base_topic") or state.get("topic") or "",
        "words": sum(len(str(s.get("narration", "")).split()) for s in scenes),
        "estimate": round(est),
        "target": round(target),
        "revision": int(state.get("gate_revision") or 0),
        "why": state.get("gate_reason") or "",
        "facts": state.get("fact_check") or None,
        "scenes": [{"purpose": s.get("purpose", ""), "narration": s.get("narration", "")}
                   for s in scenes],
        "asked_at": time.time(),
    }


def _wait_for_answer(run_id: str) -> dict:
    """Poll for a decision. Returns an answer, or 'go' once the wait runs out."""
    deadline = time.time() + MAX_WAIT
    ans_path, req_path = _ans_path(run_id), _req_path(run_id)
    if ans_path is None or req_path is None:
        # The run id cannot name a file safely, so nothing can ever answer it.
        # Carrying on is the safe failure: the render proceeds rather than
        # blocking forever on a question nobody can be asked.
        log("warn", f"run id {run_id!r} cannot be used as a filename; not stopping")
        return {"action": "go", "note": "", "unanswerable": True}
    while time.time() < deadline:
        if ans_path.exists():
            ans = read_json(ans_path, None)
            if isinstance(ans, dict) and ans.get("action"):
                return ans
            # A half-written file: give the writer a moment and look again.
        elif not req_path.exists():
            # Something outside removed the request — a cancel, or a cleanup.
            return {"action": "drop", "note": "the request was withdrawn"}
        time.sleep(1.0)
    log("warn", f"nobody read the script for {run_id} in time; rendering it")
    return {"action": "go", "note": "", "timed_out": True}


def stop_reason(state: dict) -> str:
    """Why this run should stop, or "" to carry straight on.

    Only Guided stops. Express and Full auto both promise on the Make screen
    that they render straight through, and a mode that sometimes stops anyway
    would be worse than one that never does — you could not leave it running.
    """
    if (state.get("mode") or "express").lower() != "guided":
        return ""
    return "you asked to read it first"


def node_gate(state: dict) -> dict:
    """Between writing and rendering: stop, unless the mode says otherwise."""
    from .nodes.common import progress
    from .nodes.script import node_script

    if not (state.get("script") or {}).get("scenes"):
        return {}                       # nothing written; let the run fail normally
    reason = stop_reason(state)
    if not reason:
        return {}

    run_id = str(state.get("run_id"))
    base_topic = state.get("gate_base_topic") or state.get("topic") or ""
    state["gate_base_topic"] = base_topic
    # A rewrite replaces the script, and the LangGraph engine only keeps what a
    # node RETURNS — mutating `state` in place would quietly render the version
    # the user just rejected. So everything a rewrite produces is accumulated
    # here and handed back.
    out: dict = {}

    req = _req_path(run_id)
    if req is None:
        log("warn", f"run id {run_id!r} cannot be used as a filename; not stopping")
        return out

    while True:
        state["gate_reason"] = reason
        write_json(req, _summary(state))
        progress(state, "awaiting", f"Waiting for you to read the script — {reason}",
                 extra={"gate": True, "title": (state.get("script") or {}).get("title")})
        log("info", f"script for {run_id} is waiting to be read")

        ans = _wait_for_answer(run_id)
        action = str(ans.get("action") or "go")
        note = str(ans.get("note") or "").strip()

        if action == "go":
            _clear(run_id)
            progress(state, "script", "Approved — rendering now")
            return out

        if action == "drop":
            _clear(run_id)
            progress(state, "cancelled", "You threw this script away", severity="yellow")
            raise RuntimeError("script rejected before rendering")

        # rewrite ------------------------------------------------------------
        state["gate_revision"] = int(state.get("gate_revision") or 0) + 1
        out["gate_revision"] = state["gate_revision"]
        _clear(run_id)
        progress(state, "script", f"Rewriting — {note[:120]}")
        log("info", f"rewriting {run_id} (revision {state['gate_revision']}): {note[:120]}")
        # The note rides along with the brief, which is the one input both the
        # planner and the writer already read.
        state["topic"] = (f"{base_topic}\n\nTHE LAST VERSION WAS REJECTED. "
                          f"WHAT TO CHANGE: {note}")
        try:
            update = node_script(state) or {}
        except Exception as exc:  # noqa: BLE001
            log("warn", f"rewrite failed ({exc}); keeping the previous script")
            state["topic"] = base_topic
            progress(state, "script", "The rewrite failed — rendering the version you had")
            return out
        state.update(update)
        out.update(update)
        state["topic"] = base_topic
        reason = "you asked to read it first"


__all__ = ["node_gate", "waiting", "decide", "drop_all", "stop_reason",
           "MODES", "GATE_DIR"]
