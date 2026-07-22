"""Stage 2 — voice. Famous voices first, always-works fallback.

Engine order (auto): ElevenLabs (the famous AI narrator voices — needs a free
ELEVENLABS_API_KEY) -> TikTok TTS (free, if a working endpoint is set) -> Kokoro
(free, local, unlimited) -> speech-paced placeholder tone. An engine that errors
(e.g. ElevenLabs quota) is dropped for the rest of the run; the pipeline never
fails at the voice stage.
"""
from __future__ import annotations

import base64
import json
import urllib.request
import wave
from pathlib import Path

from ..utils import (MODELS_DIR, ensure_dir, env, estimate_speech_seconds,
                     host_reachable, http_download, log, run_ffmpeg, wav_duration,
                     write_silence_wav)
from .common import add_fallback, progress

KOKORO_ONNX_URL = env("KOKORO_ONNX_URL",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx")
KOKORO_VOICES_URL = env("KOKORO_VOICES_URL",
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin")
MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"

_KOKORO = None


def _mp3_to_wav(mp3: Path, wav: Path) -> bool:
    ok, _ = run_ffmpeg(["-i", str(mp3.resolve()), "-ar", "24000", "-ac", "1",
                        str(wav.resolve())], cwd=wav.parent)
    return ok and wav.exists()


# --------------------------------------------------------------------------- #
# ElevenLabs — the famous voices (free tier with a key)
# --------------------------------------------------------------------------- #
def synth_elevenlabs(text: str, voice_id: str, out_wav: Path) -> None:
    key = env("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("no ELEVENLABS_API_KEY")
    model = env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = json.dumps({"text": text, "model_id": model,
                       "voice_settings": {"stability": 0.45, "similarity_boost": 0.8,
                                          "style": 0.35}}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=90) as r:
        mp3 = out_wav.with_suffix(".mp3")
        mp3.write_bytes(r.read())
    if not _mp3_to_wav(mp3, out_wav):
        raise RuntimeError("elevenlabs mp3->wav failed")


# --------------------------------------------------------------------------- #
# TikTok TTS — free, needs a reachable endpoint (TIKTOK_TTS_URL)
# --------------------------------------------------------------------------- #
def synth_tiktok(text: str, voice: str, out_wav: Path) -> None:
    endpoint = env("TIKTOK_TTS_URL")
    if not endpoint:
        raise RuntimeError("no TIKTOK_TTS_URL")
    body = json.dumps({"text": text[:290], "voice": voice}).encode()
    req = urllib.request.Request(endpoint, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read())
    b64 = data.get("data") or data.get("audio") or data.get("v_str")
    if not b64:
        raise RuntimeError(f"tiktok tts: no audio ({str(data)[:120]})")
    mp3 = out_wav.with_suffix(".mp3")
    mp3.write_bytes(base64.b64decode(b64))
    if not _mp3_to_wav(mp3, out_wav):
        raise RuntimeError("tiktok mp3->wav failed")


# --------------------------------------------------------------------------- #
# Kokoro — free, local, unlimited
# --------------------------------------------------------------------------- #
def _ensure_kokoro():
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
            http_download(KOKORO_VOICES_URL, VOICES_PATH, timeout=600)
        _KOKORO = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        return _KOKORO
    except Exception as exc:  # noqa: BLE001
        log("warn", f"Kokoro init failed: {exc}")
        return None


# Voice styles you can pick per video.
#   adam    = the deep classic Shorts narrator (~177 wpm, the viral 160-180 band)
#   squeaky = the chipmunk "brainrot" voice: same words, pitch shifted up and
#             sped up (that effect is post-processing, not a separate voice model)
VOICE_STYLES = {
    "adam":    {"speed": 1.18, "pitch": 1.00, "label": "Adam — deep narrator"},
    "squeaky": {"speed": 1.30, "pitch": 1.38, "label": "Squeaky — high-pitched chipmunk"},
}
DEFAULT_STYLE = "adam"


def resolve_style(state: dict, cfg: dict) -> tuple[str, dict]:
    name = (state.get("voice_style") or cfg.get("voice_style") or
            env("VOICE_STYLE", "") or DEFAULT_STYLE)
    name = str(name).strip().lower()
    aliases = {"deep": "adam", "normal": "adam", "male": "adam", "default": "adam",
               "high": "squeaky", "chipmunk": "squeaky", "funny": "squeaky",
               "brainrot": "squeaky", "helium": "squeaky"}
    name = aliases.get(name, name)
    return (name, VOICE_STYLES.get(name, VOICE_STYLES[DEFAULT_STYLE]))


def voice_speed(cfg: dict) -> float:
    """Narration pace. Shorts live or die on pace — default is deliberately fast."""
    try:
        return max(0.6, min(1.6, float(env("VOICE_SPEED", "") or cfg.get("voice_speed", 1.18))))
    except Exception:
        return 1.18


def _pitch_up(wav: Path, pitch: float, rate: int = 24000) -> None:
    """Raise pitch without changing length (asetrate up, atempo back down)."""
    if abs(pitch - 1.0) < 0.02:
        return
    tmp = wav.with_name(wav.stem + "_pt.wav")
    ok, _ = run_ffmpeg(["-i", str(wav.resolve()), "-filter:a",
                        f"asetrate={int(rate*pitch)},aresample={rate},"
                        f"atempo={1/pitch:.4f}",
                        "-ar", str(rate), "-ac", "1", str(tmp.resolve())], cwd=wav.parent)
    if ok and tmp.exists():
        tmp.replace(wav)


def _speed_up(wav: Path, speed: float) -> None:
    """Post-hoc tempo change (for engines with no native speed control)."""
    if abs(speed - 1.0) < 0.02:
        return
    tmp = wav.with_name(wav.stem + "_sp.wav")
    ok, _ = run_ffmpeg(["-i", str(wav.resolve()), "-filter:a", f"atempo={speed:.3f}",
                        "-ar", "24000", "-ac", "1", str(tmp.resolve())], cwd=wav.parent)
    if ok and tmp.exists():
        tmp.replace(wav)


def synth_kokoro(text: str, voice_id: str, out_wav: Path, speed: float = 1.0) -> None:
    import numpy as np
    kok = _ensure_kokoro()
    if kok is None:
        raise RuntimeError("kokoro unavailable")
    # Kokoro changes pace natively (better quality than post-stretching)
    samples, rate = kok.create(text, voice=voice_id, speed=speed, lang="en-us")
    s16 = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(rate))
        wf.writeframes(s16.tobytes())


# --------------------------------------------------------------------------- #
# Node
# --------------------------------------------------------------------------- #
def _engine_chain(cfg: dict, mock: bool) -> list[tuple]:
    """Ordered [(name, synth_fn, voice_id)] the run will try, best first."""
    if mock:
        return [("kokoro", synth_kokoro, cfg.get("voice", "am_adam"))]
    want = (env("VOICE_ENGINE", "auto") or "auto").lower()
    chain = []
    if want in ("auto", "elevenlabs") and env("ELEVENLABS_API_KEY"):
        chain.append(("elevenlabs", synth_elevenlabs,
                      cfg.get("voice_11", "pNInz6obpgDQGcFmaJgB")))
    if want in ("auto", "tiktok") and env("TIKTOK_TTS_URL"):
        chain.append(("tiktok", synth_tiktok, cfg.get("voice_tiktok", "en_us_002")))
    chain.append(("kokoro", synth_kokoro, cfg.get("voice", "am_adam")))
    return chain


def node_voice(state: dict) -> dict:
    cfg = state["channel_cfg"]
    scenes = state["script"]["scenes"]
    audio_dir = ensure_dir(Path(state["workdir"]) / "audio")
    mock = state.get("mock", False)

    chain = _engine_chain(cfg, mock)
    # don't reach out for Kokoro weights if we can't get online (first run only)
    if chain and chain[-1][0] == "kokoro" and not mock and not MODEL_PATH.exists() \
            and not host_reachable("https://github.com", timeout=4):
        pass
    progress(state, "voice",
             f"synthesizing {len(scenes)} clips via {'/'.join(c[0] for c in chain)}")

    dead: set[str] = set()
    audio_paths, durations = [], []
    tones = [174.0, 196.0, 220.0, 233.08, 261.63]
    used_engine = "placeholder"
    style_name, style = resolve_style(state, cfg)
    speed = round(voice_speed(cfg) * (style["speed"] / VOICE_STYLES["adam"]["speed"]), 3)
    pitch = style["pitch"]

    for i, sc in enumerate(scenes):
        out = audio_dir / f"scene_{i+1:02d}.wav"
        text = (sc.get("narration") or "").strip() or "..."
        made = False
        for name, fn, vid in chain:
            if name in dead:
                continue
            try:
                if name == "kokoro":
                    fn(text, vid, out, speed)        # native pace control
                else:
                    fn(text, vid, out)
                    _speed_up(out, speed)            # tempo-shift the rest
                _pitch_up(out, pitch)                # squeaky style only
                made, used_engine = True, name
                break
            except Exception as exc:  # noqa: BLE001
                if name != "kokoro":            # transient endpoint/quota -> drop for run
                    dead.add(name)
                    add_fallback(state, "voice", f"{name} failed ({str(exc)[:80]}); next engine")
        if not made:
            secs = estimate_speech_seconds(text, wpm=150)
            write_silence_wav(out, secs, rate=24000, tone_hz=tones[i % len(tones)], amplitude=0.05)
        audio_paths.append(str(out))
        durations.append(round(wav_duration(out) or estimate_speech_seconds(text), 3))

    progress(state, "voice", f"voice done: {len(audio_paths)} clips, "
                             f"~{round(sum(durations),1)}s ({used_engine}, "
                             f"{style['label']} @ {speed:.2f}x)")
    return {"audio_paths": audio_paths, "durations": durations,
            "voice_engine": used_engine, "voice_style": style_name}
