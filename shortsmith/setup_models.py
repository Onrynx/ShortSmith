"""Fetch everything a render needs, in one go.

Three models have to be on disk before a video can be made: the writer, the
narrator's voice, and the one that times the captions to that voice. Until now
only the first was asked for. The other two were fetched lazily, the first time
a render happened to reach their stage — so the sequence was:

    pick a model, wait for 8 GB, think setup is finished, press Make,
    and have the render stop twice for half a gigabyte it never mentioned

Worse, those two bypassed the metered-connection check entirely, because that
check only guarded the download somebody chose. The one they did not choose
went out over their mobile data without a word.

So all three are fetched together, behind one progress bar, before anything
claims to be ready. The total is what gets checked against the connection, and
when it finishes the app can honestly say a render will work offline.

Each piece keeps the integrity check it already had: the writer model comes
through Ollama, which verifies its own digests, and the voice files are checked
against the SHA-256 pinned in nodes/voice.py.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

from .utils import log

# What the screen polls. One shape for all three stages so the bar never has to
# know which thing is being fetched.
STATE: dict = {
    "status": "idle",          # idle | running | done | failed
    "stage": "",               # writer | voice | captions
    "label": "",               # what to show under the bar
    "step": 0, "steps": 4,
    "percent": 0.0,            # within the current stage
    "overall": 0.0,            # across all three
    "done_gb": 0.0, "total_gb": 0.0,
    "model": None, "error": None, "finished_at": None,
}
_lock = threading.Lock()

# Rough shares of the total, used only to keep the overall bar honest while a
# stage that cannot report bytes is running.
_WEIGHTS = {"writer": 0.80, "ffmpeg": 0.10, "voice": 0.07, "captions": 0.03}


def state() -> dict:
    with _lock:
        return dict(STATE)


def _set(**kw) -> None:
    with _lock:
        STATE.update(kw)
        # Overall = finished stages + progress through the current one.
        order = ["writer", "ffmpeg", "voice", "captions"]
        stage = STATE.get("stage")
        done = 0.0
        if stage in order:
            for s in order[:order.index(stage)]:
                done += _WEIGHTS[s]
            done += _WEIGHTS[stage] * (STATE.get("percent", 0.0) / 100.0)
        STATE["overall"] = round(min(100.0, done * 100), 1)


# --------------------------------------------------------------------------- #
# 1. the writer model, via Ollama
# --------------------------------------------------------------------------- #
def _pull_writer(model: str) -> None:
    from .llm import ollama_base_url
    base = ollama_base_url() or "http://localhost:11434"
    _set(stage="writer", step=1, percent=0.0, model=model,
         label=f"Downloading {model}")
    req = urllib.request.Request(
        f"{base}/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=None) as resp:
        for raw in resp:
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if msg.get("error"):
                raise RuntimeError(str(msg["error"]))
            done, total = msg.get("completed"), msg.get("total")
            if done and total:
                _set(percent=round(done / total * 100, 1),
                     done_gb=round(done / 1024 ** 3, 2),
                     total_gb=round(total / 1024 ** 3, 2),
                     label=f"Downloading {model}")
    _set(percent=100.0)


# --------------------------------------------------------------------------- #
# 2. the voice
# --------------------------------------------------------------------------- #
def _pull_ffmpeg() -> None:
    """The video encoder. Nothing renders without it, and the bundle has none.

    Skipped when the machine already has one. Many do not — and that was the
    hole: everything else was fetched, nine gigabytes spent, and the render
    failed at the cutter with "FFmpeg not found on PATH".
    """
    from .ffmpeg_setup import have_any, install
    _set(stage="ffmpeg", step=2, percent=0.0, label="Checking for the video encoder")
    if have_any():
        log("info", "ffmpeg is already on this machine; not downloading one")
        _set(percent=100.0, label="Video encoder already installed")
        return
    r = install(on_progress=lambda m: _set(percent=40.0, label=m))
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "could not install ffmpeg")
    _set(percent=100.0)


def _pull_voice() -> None:
    from .nodes import voice as V
    _set(stage="voice", step=3, percent=0.0, label="Downloading the narration voice")
    pairs = [(V.KOKORO_ONNX_URL, V.MODEL_PATH, V.KOKORO_ONNX_SHA256),
             (V.KOKORO_VOICES_URL, V.VOICES_PATH, V.KOKORO_VOICES_SHA256)]
    for i, (url, path, digest) in enumerate(pairs):
        if path.exists() and path.stat().st_size > 1024:
            _set(percent=round((i + 1) / len(pairs) * 100, 1))
            continue
        from .utils import http_download
        http_download(url, path, timeout=1800,
                      expect_sha256=V._expected(url, digest),
                      max_bytes=600 * 1024 ** 2)
        _set(percent=round((i + 1) / len(pairs) * 100, 1))
    _set(percent=100.0)


# --------------------------------------------------------------------------- #
# 3. caption timing
# --------------------------------------------------------------------------- #
def _pull_captions() -> None:
    """Load the whisper model once so its weights land in the cache.

    faster-whisper downloads on first construction, so simply building it is
    the download. If the library is missing the pipeline still works — captions
    fall back to timings estimated from the measured audio — so this is not
    allowed to fail the setup.
    """
    import os
    _set(stage="captions", step=4, percent=0.0,
         label="Downloading the caption timer")
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        log("info", "faster-whisper is not installed; captions will be estimated")
        _set(percent=100.0)
        return
    name = os.environ.get("WHISPER_MODEL", "base")
    WhisperModel(name, device="cpu", compute_type="int8")
    _set(percent=100.0)


# --------------------------------------------------------------------------- #
def _run(model: str) -> None:
    started = time.time()
    try:
        _pull_writer(model)
        _pull_ffmpeg()
        _pull_voice()
        _pull_captions()
    except Exception as exc:  # noqa: BLE001
        log("warn", f"setup download failed during {STATE.get('stage')}: {exc}")
        _set(status="failed", error=str(exc)[:300],
             label=f"Could not finish: {str(exc)[:120]}")
        return
    _set(status="done", percent=100.0, overall=100.0, finished_at=time.time(),
         label="Everything is ready")
    log("info", f"setup complete in {time.time() - started:.0f}s "
                f"({model}, voice and caption timing)")


def start(model: str) -> dict:
    """Begin fetching all three. Returns immediately."""
    with _lock:
        if STATE.get("status") == "running":
            return {"ok": False, "error": "a download is already running"}
        STATE.update(status="running", stage="writer", step=0, percent=0.0,
                     overall=0.0, done_gb=0.0, total_gb=0.0, model=model,
                     error=None, finished_at=None, label="Starting")
    threading.Thread(target=_run, args=(model,), daemon=True).start()
    return {"ok": True, "model": model}


def total_gb(model_tag: str) -> float:
    """Everything that will be fetched, for the connection warning.

    The writer model's own size plus the two the user did not pick — which is
    the number that matters when somebody is on a metered connection, and the
    number the old check left out.
    """
    from .model_profiles import CATALOG
    entry = next((e for e in CATALOG if e["tag"] == model_tag), None)
    writer = float(entry.get("download_gb") or 0) if entry else 0.0
    return round(writer + VOICE_GB + CAPTIONS_GB, 2)


VOICE_GB = 0.34        # kokoro onnx + voices
CAPTIONS_GB = 0.15     # faster-whisper "base"


def ready() -> dict:
    """Is everything a render needs already on disk?"""
    from .llm import select_model
    from .nodes import voice as V

    from .ffmpeg_setup import have_any
    writer = select_model(False)
    have_writer = writer.get("backend") == "ollama"
    have_encoder = have_any()
    have_voice = (V.MODEL_PATH.exists() and V.VOICES_PATH.exists()
                  and V.MODEL_PATH.stat().st_size > 1024)
    missing = []
    if not have_writer:
        missing.append("a writer model")
    if not have_voice:
        missing.append("the narration voice")
    if not have_encoder:
        missing.append("the video encoder")
    return {"ready": not missing, "missing": missing,
            "writer": writer.get("model") if have_writer else None,
            "voice": have_voice, "ffmpeg": have_encoder}


__all__ = ["start", "state", "ready", "total_gb", "VOICE_GB", "CAPTIONS_GB"]
