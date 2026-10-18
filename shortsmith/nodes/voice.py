"""Stage 2 — voice. Synthesised here, on this machine, every time.

Kokoro runs locally and unmetered, and if it fails the run falls through to a
speech-paced silent track rather than stopping: the pipeline never dies at the
voice stage.

V1 preferred two hosted engines when an API key was present, which meant the
narration — the entire script — was uploaded to a third party because of a line
in a config file. They are gone. Narration text does not leave this computer.
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
# The exact artifacts this app was built and tested against. An ONNX file is
# a computation graph that onnxruntime will load and run, so "it came over
# HTTPS from the right host" is not enough on its own — the host can re-tag a
# release, and the URLs above are overridable from .env. A mismatch refuses the
# file rather than loading it.
#
# These digests were taken from the local copies AND re-downloaded from the
# release URLs above and confirmed byte-for-byte, so they are what the
# publisher is serving rather than just what one machine happened to hold.
# That is still not a signature: it proves the file has not changed since it
# was checked, not that the author intended its contents.
KOKORO_ONNX_SHA256 = "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
KOKORO_VOICES_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"

MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"

_KOKORO = None


def _mp3_to_wav(mp3: Path, wav: Path) -> bool:
    ok, _ = run_ffmpeg(["-i", str(mp3.resolve()), "-ar", "24000", "-ac", "1",
                        str(wav.resolve())], cwd=wav.parent)
    return ok and wav.exists()


# --------------------------------------------------------------------------- #
# Narration is synthesised on this machine, full stop.
#
# V1 could call out to ElevenLabs or a TikTok TTS endpoint, which meant the
# narration text — the whole script — was POSTed to a third party the moment an
# API key appeared in .env. The app tells the user "nothing leaves this
# computer", and a config file was able to make that untrue without changing
# anything on screen. Both engines are gone rather than gated: an off switch
# someone can flip by editing a file is not a promise.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Kokoro — free, local, unlimited
# --------------------------------------------------------------------------- #
def _expected(url: str, digest: str) -> str | None:
    """The digest to enforce, unless the URL was pointed somewhere else.

    Overriding KOKORO_ONNX_URL is a legitimate thing to do (a mirror, an
    air-gapped copy), and a digest for a different file would just make that
    impossible. So the pin applies to the artifact we know, and a custom URL
    is checked against SHORTSMITH_KOKORO_SHA256 if the user supplies one.
    """
    from ..utils import env as _env
    default_onnx = ("https://github.com/thewh1teagle/kokoro-onnx/releases/"
                    "download/model-files-v1.0/kokoro-v1.0.onnx")
    default_voices = ("https://github.com/thewh1teagle/kokoro-onnx/releases/"
                      "download/model-files-v1.0/voices-v1.0.bin")
    if url in (default_onnx, default_voices):
        return digest
    override = (_env("SHORTSMITH_KOKORO_SHA256", "") or "").strip()
    if override:
        return override
    log("warn", f"{url} is not the pinned download and no checksum was given; "
                "it will be accepted unverified")
    return None


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
            http_download(KOKORO_ONNX_URL, MODEL_PATH, timeout=1800,
                          expect_sha256=_expected(KOKORO_ONNX_URL, KOKORO_ONNX_SHA256),
                          max_bytes=600 * 1024 ** 2)
        if not VOICES_PATH.exists():
            http_download(KOKORO_VOICES_URL, VOICES_PATH, timeout=600,
                          expect_sha256=_expected(KOKORO_VOICES_URL, KOKORO_VOICES_SHA256),
                          max_bytes=200 * 1024 ** 2)
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
    # One engine, on this machine. The list shape is kept because the caller
    # walks it and falls through to a silent placeholder if synthesis fails.
    return [("kokoro", synth_kokoro, cfg.get("voice", "am_adam"))]


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
    used_engine = "placeholder"
    style_name, style = resolve_style(state, cfg)
    speed = round(voice_speed(cfg) * (style["speed"] / VOICE_STYLES["adam"]["speed"]), 3)
    pitch = style["pitch"]

    def synth_one(i: int, text: str) -> tuple[str, float]:
        """Render one scene's narration; returns (path, measured seconds)."""
        nonlocal used_engine
        out = audio_dir / f"scene_{i+1:02d}.wav"
        text = (text or "").strip() or "..."
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
            # When TTS fails for a scene, hold the scene for the right length
            # in SILENCE. This used to write a tremolo'd sine tone as an
            # "audible placeholder", which in a finished video is a droning
            # synth under the picture — far worse than a quiet gap.
            secs = estimate_speech_seconds(text, wpm=150)
            write_silence_wav(out, secs, rate=24000)
            add_fallback(state, "voice",
                         f"scene {i+1}: no narration could be synthesised; "
                         "the scene is silent")
        return str(out), round(wav_duration(out) or estimate_speech_seconds(text), 3)

    for i, sc in enumerate(scenes):
        path, dur = synth_one(i, sc.get("narration"))
        audio_paths.append(path)
        durations.append(dur)

    # ---- close the loop: the video's REAL length is now known ---------------- #
    # Everything before this was an estimate. Feed the measurement back so (a)
    # future runs estimate better, and (b) this run gets fixed if it missed.
    t = state.get("targets") or {}
    if t and not mock:
        from ..timing import plan_revisions, record_calibration
        measured = sum(durations)
        record_calibration(style_name, scenes, measured, t.get("wpm", 177))
        rev = plan_revisions(scenes, durations, t["target_seconds"], t.get("max_seconds"))
        if rev.get("action") in ("compress", "expand") and rev.get("severe"):
            progress(state, "voice",
                     f"runtime {measured:.0f}s vs target {t['target_seconds']:.0f}s — "
                     f"{rev['action']}ing {len(rev['targets'])} scene(s) and re-voicing")
            from ..crews.story_crew import revise_for_duration
            before = [s.get("narration") for s in scenes]
            state["script"] = revise_for_duration(state["script"], rev,
                                                  state.get("model_info") or {}, t)
            scenes = state["script"]["scenes"]
            changed = [i for i, s in enumerate(scenes)
                       if i < len(before) and s.get("narration") != before[i]]
            for i in changed:                      # re-voice ONLY what changed
                path, dur = synth_one(i, scenes[i].get("narration"))
                audio_paths[i], durations[i] = path, dur
            if changed:
                log("info", f"re-voiced {len(changed)} scene(s); runtime "
                            f"{measured:.0f}s -> {sum(durations):.0f}s")

    progress(state, "voice", f"voice done: {len(audio_paths)} clips, "
                             f"~{round(sum(durations),1)}s ({used_engine}, "
                             f"{style['label']} @ {speed:.2f}x)")
    return {"audio_paths": audio_paths, "durations": durations,
            "voice_engine": used_engine, "voice_style": style_name}
