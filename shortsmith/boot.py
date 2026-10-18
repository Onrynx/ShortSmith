"""Startup — bring the AI up when the app opens, and take it down after.

Ollama normally installs itself as a login item and sits in memory all day
holding a multi-gigabyte model, whether or not you are making videos. That is a
poor deal on a laptop. Shortsmith starts it when the app opens and stops it
again on the way out — but only if Shortsmith was the one that started it, so a
machine where Ollama is deliberately always-on is left alone.

Progress is reported honestly. Four of the five stages are real checks with
real completion points. The fifth — loading the model into memory — gives no
progress signal at all, so instead of inventing a spinner we time how long it
took last time and drive the bar from that, holding at 99% until the model
genuinely answers. The estimate improves every launch.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .utils import CREATE_NO_WINDOW, STATE_DIR, log, read_json, write_json

_TIMINGS = STATE_DIR / "boot_timing.json"
_OLLAMA_PID = STATE_DIR / "ollama.pid"
_DEFAULT_WARM = 25.0          # seconds, before we have measured a real one

# Live state the UI polls.
BOOT: dict = {
    "stage": "idle", "percent": 0.0, "message": "", "ready": False,
    "error": None, "model": None, "started_ollama": False, "elapsed": 0.0,
}
_proc: list = []              # the Ollama process, when we started it
_lock = threading.Lock()


def _base_url() -> str:
    from .llm import ollama_base_url
    return ollama_base_url()


def _get(path: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(f"{_base_url()}{path}", timeout=timeout) as r:
            import json
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def _set(stage: str, percent: float, message: str) -> None:
    with _lock:
        BOOT.update(stage=stage, percent=round(percent, 1), message=message)


def _ollama_binary() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path("/usr/local/bin/ollama"),
        Path("/opt/homebrew/bin/ollama"),
        Path("/Applications/Ollama.app/Contents/Resources/ollama"),
    ]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def _adopt_orphan() -> bool:
    """True if the running Ollama is an orphan from a previous Shortsmith run.

    A force-quit or a crash skips cleanup, so the engine can be left resident.
    Recording the pid we spawn lets the next launch recognise and re-own it
    instead of leaving it running for the rest of the day.
    """
    try:
        pid = int((_OLLAMA_PID.read_text() or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        return False
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore
        if not psutil.pid_exists(pid):
            return False
        name = psutil.Process(pid).name().lower()
    except Exception:  # noqa: BLE001
        return False
    if "ollama" not in name:
        return False

    class _Adopted:
        def __init__(self, pid):
            self.pid = pid

        def poll(self):
            try:
                import psutil
                return None if psutil.pid_exists(self.pid) else 0
            except Exception:  # noqa: BLE001
                return 0

    _proc.append(_Adopted(pid))
    log("info", f"adopted an orphaned AI engine (pid {pid}) from a previous run")
    return True


def _warm_estimate() -> float:
    data = read_json(_TIMINGS, {}) or {}
    try:
        return max(4.0, min(180.0, float(data.get("warm_seconds", _DEFAULT_WARM))))
    except Exception:  # noqa: BLE001
        return _DEFAULT_WARM


def _record_warm(seconds: float) -> None:
    data = read_json(_TIMINGS, {}) or {}
    prev = float(data.get("warm_seconds", seconds) or seconds)
    # ease toward the new measurement so one cold boot doesn't skew it
    data["warm_seconds"] = round(prev + 0.5 * (seconds - prev), 2)
    try:
        write_json(_TIMINGS, data)
    except Exception:  # noqa: BLE001
        pass


def _warm_model(model: str) -> None:
    """Ask for one token so the weights are resident before the user clicks."""
    import json
    payload = json.dumps({"model": model, "prompt": "hi",
                          "stream": False, "options": {"num_predict": 1}}).encode()
    req = urllib.request.Request(f"{_base_url()}/api/generate", data=payload,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=600).read()


def _run() -> None:
    t0 = time.time()
    try:
        # ---- 1. is it already up? ------------------------------------- #
        _set("checking", 4, "Looking for the AI engine")
        already = _get("/api/tags", timeout=2) is not None

        if already:
            _set("starting", 30, "AI engine already running")
            # If it is one WE started and a previous run was force-quit before
            # it could clean up, adopt it so this session shuts it down.
            if _adopt_orphan():
                with _lock:
                    BOOT["started_ollama"] = True
        else:
            binary = _ollama_binary()
            if not binary:
                with _lock:
                    BOOT.update(stage="failed", error=(
                        "Ollama is not installed. Get it from ollama.com/download, "
                        "then reopen Shortsmith."), message="Ollama not found")
                return
            _set("starting", 12, "Starting the AI engine")
            kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
            proc = subprocess.Popen([binary, "serve"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, **kw)
            _proc.append(proc)
            with _lock:
                BOOT["started_ollama"] = True
            try:
                _OLLAMA_PID.write_text(str(proc.pid))
            except Exception:  # noqa: BLE001
                pass

            # ---- 2. wait for the API ---------------------------------- #
            deadline = time.time() + 60
            while time.time() < deadline:
                if _get("/api/tags", timeout=2) is not None:
                    break
                _set("starting", min(28, 12 + (time.time() - t0) * 2),
                     "Starting the AI engine")
                time.sleep(0.7)
            else:
                with _lock:
                    BOOT.update(stage="failed", message="AI engine did not start",
                                error="Ollama did not respond within a minute.")
                return

        # ---- 3. which model, and is it installed? --------------------- #
        _set("model", 38, "Checking the writer model")
        from .llm import select_model
        info = select_model(False)
        model = info.get("model") or ""
        with _lock:
            BOOT["model"] = model
        tags = _get("/api/tags") or {}
        have = [m.get("name", "") for m in tags.get("models", [])]
        if not have:
            with _lock:
                BOOT.update(stage="failed", message="No model installed",
                            error=("No writer model is installed yet. Open "
                                   "Settings and download one."))
            return

        # ---- 4. load it into memory ----------------------------------- #
        est = _warm_estimate()
        _set("warming", 45, f"Loading {model}")
        warm_start = time.time()
        done = threading.Event()

        def _progress():
            # No load percentage exists, so drive the bar from how long this
            # took last time and hold at 99 until the model actually answers.
            while not done.wait(0.4):
                frac = min(0.99, (time.time() - warm_start) / max(est, 1.0))
                _set("warming", 45 + frac * 54, f"Loading {model}")

        threading.Thread(target=_progress, daemon=True).start()
        try:
            _warm_model(model)
        except Exception as exc:  # noqa: BLE001
            done.set()
            log("warn", f"model warm-up failed: {exc}")
            # Not fatal: the first render will simply load it instead.
            with _lock:
                BOOT.update(stage="ready", percent=100.0, ready=True,
                            message="Ready (model loads on first video)",
                            elapsed=round(time.time() - t0, 1))
            return
        done.set()
        _record_warm(time.time() - warm_start)

        with _lock:
            BOOT.update(stage="ready", percent=100.0, ready=True,
                        message="Ready", elapsed=round(time.time() - t0, 1))
        log("info", f"startup complete in {time.time() - t0:.1f}s ({model} loaded)")
    except Exception as exc:  # noqa: BLE001
        log("warn", f"startup failed: {exc}")
        with _lock:
            BOOT.update(stage="failed", error=str(exc), message="Startup failed")


def start() -> None:
    """Kick off startup in the background. Safe to call more than once."""
    if BOOT.get("stage") not in ("idle", "failed"):
        return
    BOOT.update(stage="checking", percent=0.0, ready=False, error=None)
    threading.Thread(target=_run, daemon=True).start()


def state() -> dict:
    with _lock:
        s = dict(BOOT)
    s["warm_estimate"] = _warm_estimate()
    return s


def shutdown() -> None:
    """Stop the AI engine, but only if we were the ones who started it."""
    if not BOOT.get("started_ollama"):
        return
    for proc in _proc:
        try:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True)
                else:
                    proc.terminate()
                log("info", "AI engine stopped")
        except Exception as exc:  # noqa: BLE001
            log("warn", f"could not stop the AI engine: {exc}")
    _proc.clear()
    BOOT["started_ollama"] = False
    try:
        _OLLAMA_PID.unlink()
    except Exception:  # noqa: BLE001
        pass
