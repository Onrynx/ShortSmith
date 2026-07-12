"""Stage 2 — voice.

Kokoro (kokoro-onnx, open weights, commercial-safe) is the cinematic AI-narrator
voice. The ~340MB model+voices auto-download into ./models on first run. Per-channel
voice id. One wav per scene; durations recorded. If Kokoro is unavailable or fails,
a speech-paced placeholder tone is written so the pipeline always completes.
"""
from __future__ import annotations

import wave
from pathlib import Path

from ..utils import (MODELS_DIR, ensure_dir, env, estimate_speech_seconds,
                     host_reachable, http_download, log, wav_duration,
                     write_silence_wav)
from .common import add_fallback, progress

KOKORO_ONNX_URL = env(
    "KOKORO_ONNX_URL",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
)
KOKORO_VOICES_URL = env(
    "KOKORO_VOICES_URL",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
)
MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"

_KOKORO = None  # cached instance


def _write_wav(path: Path, samples, rate: int) -> None:
    import numpy as np
    if samples.dtype != np.int16:
        samples = np.clip(samples, -1.0, 1.0)
        samples = (samples * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())


def _ensure_kokoro():
    """Return a cached Kokoro instance, downloading weights on first use. None on failure."""
    global _KOKORO
    if _KOKORO is not None:
        return _KOKORO
    try:
        from kokoro_onnx import Kokoro  # type: ignore
    except Exception:
        return None
    try:
        if not MODEL_PATH.exists():
            log("info", "downloading Kokoro model (~310MB) ...")
            http_download(KOKORO_ONNX_URL, MODEL_PATH, timeout=1800)
        if not VOICES_PATH.exists():
            log("info", "downloading Kokoro voices (~28MB) ...")
            http_download(KOKORO_VOICES_URL, VOICES_PATH, timeout=600)
        _KOKORO = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        return _KOKORO
    except Exception as exc:  # noqa: BLE001
        log("warn", f"Kokoro init failed: {exc}")
        return None


def node_voice(state: dict) -> dict:
    cfg = state["channel_cfg"]
    scenes = state["script"]["scenes"]
    workdir = Path(state["workdir"])
    audio_dir = ensure_dir(workdir / "audio")
    voice_id = cfg.get("voice", "bm_george")
    mock = state.get("mock", False)

    progress(state, "voice", f"synthesizing {len(scenes)} narration clips (voice={voice_id})")

    kok = None
    if not mock:
        # only reach out for weights if we can actually talk to GitHub
        if MODEL_PATH.exists() or host_reachable("https://github.com", timeout=4):
            kok = _ensure_kokoro()
        if kok is None:
            add_fallback(state, "voice", "Kokoro unavailable; using speech-paced placeholder tone")

    audio_paths: list[str] = []
    durations: list[float] = []
    placeholder_tones = [174.0, 196.0, 220.0, 233.08, 261.63]  # gentle, varied per scene

    for i, sc in enumerate(scenes):
        out = audio_dir / f"scene_{i+1:02d}.wav"
        text = sc.get("narration", "").strip() or "..."
        made = False
        if kok is not None:
            try:
                samples, rate = kok.create(text, voice=voice_id, speed=1.0, lang="en-us")
                _write_wav(out, samples, int(rate))
                made = True
            except Exception as exc:  # noqa: BLE001
                add_fallback(state, "voice", f"Kokoro failed on scene {i+1} ({exc}); placeholder tone")
        if not made:
            secs = estimate_speech_seconds(text, wpm=150)
            tone = placeholder_tones[i % len(placeholder_tones)]
            write_silence_wav(out, secs, rate=24000, tone_hz=tone, amplitude=0.05)

        dur = wav_duration(out) or estimate_speech_seconds(text)
        audio_paths.append(str(out))
        durations.append(round(dur, 3))

    total = round(sum(durations), 1)
    progress(state, "voice", f"voice done: {len(audio_paths)} clips, ~{total}s "
                             f"({'kokoro' if kok else 'placeholder'})")
    return {"audio_paths": audio_paths, "durations": durations}
