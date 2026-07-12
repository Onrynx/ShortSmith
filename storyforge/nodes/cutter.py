"""Stage 4 — cutter (FFmpeg assembly).

Per scene we render a 1080p segment that lasts exactly the scene's narration:
  * stills (gradient cards) get a slow zoom/pan so it's never a static slideshow;
  * motion clips (stock / generated) are scaled+cropped to fill, looped/trimmed.
Each segment carries its own narration audio, so concatenation is inherently in
sync. A final pass mixes a low CC0 ambient bed under the voice and burns captions
(word-level via faster-whisper if present, else scene-text cues).

Robustness: every segment has a solid-color fallback; the final pass retries
without captions, then without music, so an mp4 is always produced.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

from ..config import format_spec
from ..utils import (ASSETS_DIR, ensure_dir, has_ffmpeg, log, run_ffmpeg)
from .common import add_error, add_fallback, progress

FPS = 30
MUSIC_PATH = ASSETS_DIR / "ambient_bed.wav"


# --------------------------------------------------------------------------- #
# Ambient music bed (synthesized CC0 fallback if the shipped asset is missing)
# --------------------------------------------------------------------------- #
def synth_ambient_bed(path: Path, seconds: int = 60, rate: int = 44100) -> Path:
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * rate)) / rate
    chord = [110.0, 164.81, 220.0, 277.18]  # A minor-ish pad
    sig = np.zeros_like(t)
    for k, f in enumerate(chord):
        lfo = 0.5 + 0.5 * np.sin(2 * np.pi * (0.03 + 0.01 * k) * t)  # slow swell
        sig += (0.16 / (k + 1)) * np.sin(2 * np.pi * f * t) * lfo
    sig += 0.01 * np.random.default_rng(7).standard_normal(t.shape)  # air
    sig /= (np.max(np.abs(sig)) + 1e-6)
    sig *= 0.5
    stereo = np.stack([sig, np.roll(sig, 400)], axis=1)  # subtle width
    samples = (stereo * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return path


def _ensure_music() -> Path | None:
    try:
        if not MUSIC_PATH.exists():
            log("info", "synthesizing CC0 ambient music bed ...")
            synth_ambient_bed(MUSIC_PATH)
        return MUSIC_PATH
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not prepare music bed: {exc}")
        return None


# --------------------------------------------------------------------------- #
# Captions
# --------------------------------------------------------------------------- #
def _fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap(text: str, width: int = 42) -> str:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\\N".join(lines[:2])


def _scene_cues(scenes, durations, chunk=8):
    """Split each scene's narration into ~`chunk`-word cues, timed proportionally.
    Shorts use small chunks (punchy word-style captions)."""
    cues = []
    t = 0.0
    for i, sc in enumerate(scenes):
        dur = durations[i] if i < len(durations) else 4.0
        words = (sc.get("narration") or "").split()
        chunks = [" ".join(words[j:j + chunk]) for j in range(0, len(words), chunk)] or [sc.get("narration", "")]
        total_w = sum(len(c.split()) for c in chunks) or 1
        ct = t
        for c in chunks:
            d = dur * (len(c.split()) / total_w)
            cues.append((ct, ct + max(0.4, d), c))
            ct += d
        t += dur
    return cues


def _cues_to_srt(cues) -> str:
    out = []
    for n, (start, end, text) in enumerate(cues, 1):
        out.append(f"{n}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{_wrap(text)}\n")
    return "\n".join(out)


def _word_srt_via_whisper(audio_paths, durations, workdir) -> str | None:
    """Word-level captions if faster-whisper is installed. Returns SRT or None."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
        import numpy as np
    except Exception:
        return None
    try:
        # build one narration track (concat scene wavs) for transcription
        pcm = []
        rate = 24000
        for p in audio_paths:
            with wave.open(p, "rb") as wf:
                rate = wf.getframerate()
                pcm.append(np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2"))
        if not pcm:
            return None
        audio = np.concatenate(pcm).astype("float32") / 32768.0
        model = WhisperModel(os.environ.get("WHISPER_MODEL", "tiny"),
                             device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio, word_timestamps=True, language="en")
        cues = []
        for seg in segments:
            for wobj in (seg.words or []):
                cues.append((wobj.start, wobj.end, wobj.word.strip()))
        # group into ~5-word cues for readability
        grouped = []
        i = 0
        while i < len(cues):
            grp = cues[i:i + 5]
            grouped.append((grp[0][0], grp[-1][1], " ".join(w for _, _, w in grp)))
            i += 5
        return _cues_to_srt(grouped) if grouped else None
    except Exception as exc:  # noqa: BLE001
        log("warn", f"whisper captions failed: {exc}")
        return None


# --------------------------------------------------------------------------- #
# Segments
# --------------------------------------------------------------------------- #
def _abs_fwd(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/")


def _build_segment(idx, visual, kind, audio, dur, seg_dir, workdir, w, h, atempo=1.0) -> Path:
    """Render one scene segment at w x h lasting `dur`. `atempo` time-fits the
    narration (>1 speeds it up) so a Short stays under its duration cap."""
    seg = seg_dir / f"seg_{idx:02d}.mp4"
    frames = int(round(dur * FPS)) + 2
    afilter = f"[1:a]atempo={max(0.5, min(2.0, atempo)):.4f}[a]"
    tail = ["-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "24000",
            "-ac", "1", "-t", f"{dur:.3f}", _abs_fwd(seg)]

    def _solid():
        args = ["-f", "lavfi", "-i", f"color=c=0x0d1117:s={w}x{h}:r={FPS}:d={dur:.3f}",
                "-i", _abs_fwd(Path(audio)), "-filter_complex", afilter,
                "-map", "0:v", "-map", "[a]", *tail]
        return run_ffmpeg(args, cwd=workdir)

    if kind == "color" or not visual:
        ok, err = _solid()
        if ok:
            return seg
        raise RuntimeError(f"segment {idx} solid failed: {err[-160:]}")

    if kind == "image":
        # slow centered zoom (Ken Burns) on an upscaled canvas to hide jitter
        vf = (f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2},"
              f"zoompan=z='min(1+0.0009*on,1.18)':x='iw/2-(iw/zoom/2)':"
              f"y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},format=yuv420p")
        args = ["-loop", "1", "-i", _abs_fwd(Path(visual)), "-i", _abs_fwd(Path(audio)),
                "-filter_complex", f"[0:v]{vf}[v];{afilter}", "-map", "[v]", "-map", "[a]", *tail]
    else:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"fps={FPS},setsar=1,format=yuv420p")
        args = ["-stream_loop", "-1", "-i", _abs_fwd(Path(visual)), "-i", _abs_fwd(Path(audio)),
                "-filter_complex", f"[0:v]{vf}[v];{afilter}", "-map", "[v]", "-map", "[a]", *tail]

    ok, err = run_ffmpeg(args, cwd=workdir)
    if ok:
        return seg
    log("warn", f"segment {idx} primary render failed ({err[-160:]}); solid-color fallback")
    ok2, err2 = _solid()
    if not ok2:
        raise RuntimeError(f"segment {idx} failed entirely: {err2[-200:]}")
    return seg


# --------------------------------------------------------------------------- #
# Node
# --------------------------------------------------------------------------- #
def node_cutter(state: dict) -> dict:
    if not has_ffmpeg():
        add_error(state, "cutter", "FFmpeg not found on PATH — cannot assemble video")
        raise RuntimeError("ffmpeg missing")

    workdir = Path(state["workdir"])
    scenes = state["script"]["scenes"]
    durations = state.get("durations", [])
    audio_paths = state.get("audio_paths", [])
    visual_paths = state.get("visual_paths", [])
    visual_kinds = state.get("visual_kinds", [])
    seg_dir = ensure_dir(workdir / "segments")

    spec = format_spec(state["channel_cfg"])
    w, h, max_seconds = spec["width"], spec["height"], spec["max_seconds"]

    # Duration cap: Shorts must stay under ~60s. If the narration overshoots,
    # time-fit everything with a speed factor (audio atempo + shorter segments).
    total_raw = sum(durations) if durations else 0.0
    factor = 1.0
    if total_raw > max_seconds and total_raw > 0:
        factor = min(2.0, round(total_raw / max_seconds, 3))
        add_fallback(state, "cutter",
                     f"runtime {total_raw:.0f}s > {max_seconds}s cap; speeding {factor:.2f}x "
                     f"to fit {spec['format']}")
    edur = [round(d / factor, 3) for d in durations]

    progress(state, "cutter",
             f"assembling {len(scenes)} scenes @ {w}x{h} ({spec['format']}, ~{sum(edur):.0f}s)")

    # 1) segments
    segs: list[Path] = []
    for i in range(len(scenes)):
        dur = edur[i] if i < len(edur) else 4.0
        visual = visual_paths[i] if i < len(visual_paths) else None
        kind = visual_kinds[i] if i < len(visual_kinds) else "image"
        audio = audio_paths[i] if i < len(audio_paths) else None
        if not visual or not audio:
            add_fallback(state, "cutter", f"scene {i+1} missing inputs; solid-color segment")
            kind = "color"
        try:
            seg = _build_segment(i, visual, kind, audio, dur, seg_dir, workdir, w, h, atempo=factor)
            segs.append(seg)
        except Exception as exc:  # noqa: BLE001
            add_fallback(state, "cutter", f"scene {i+1} segment failed ({exc}); skipping scene")

    if not segs:
        add_error(state, "cutter", "no segments could be rendered")
        raise RuntimeError("no segments")

    # 2) concat list
    list_path = seg_dir / "list.txt"
    list_path.write_text("".join(f"file '{_abs_fwd(s)}'\n" for s in segs), encoding="utf-8")

    # 3) captions (word-style small cues for Shorts)
    chunk = 4 if spec["format"] == "shorts" else 8
    captions_kind = "scene"
    srt = None
    if not state.get("mock") and factor == 1.0:
        srt = _word_srt_via_whisper(audio_paths, edur, workdir)
        if srt:
            captions_kind = "word"
    if not srt:
        srt = _cues_to_srt(_scene_cues(scenes, edur, chunk=chunk))
        captions_kind = "scene"
    (workdir / "captions.srt").write_text(srt, encoding="utf-8")

    # 4) final assembly (music + captions), with graceful degradation
    music = _ensure_music()
    total = round(sum(edur), 3) if edur else None
    final = workdir / f"{state['run_id']}.mp4"

    # Big, high captions for vertical Shorts; standard lower-third for long/horizontal.
    if spec["vertical"]:
        style = ("FontName=Arial,Fontsize=15,Bold=1,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H00101010,BorderStyle=1,Outline=3,Shadow=1,"
                 "Alignment=2,MarginV=90")
    else:
        style = ("FontName=Arial,Fontsize=16,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H00101010,BorderStyle=1,Outline=2,Shadow=1,"
                 "Alignment=2,MarginV=48")
    sub_filter = f"subtitles=captions.srt:force_style='{style}'"

    def _final_args(use_subs: bool, use_music: bool) -> list[str]:
        inputs = ["-f", "concat", "-safe", "0", "-i", "segments/list.txt"]
        if use_music and music:
            inputs += ["-stream_loop", "-1", "-i", _abs_fwd(music)]
        parts = []
        vlabel = "0:v"
        if use_subs:
            parts.append(f"[0:v]{sub_filter}[v]")
            vlabel = "[v]"
        if use_music and music:
            parts.append("[1:a]volume=0.09,aformat=sample_rates=48000:channel_layouts=stereo[bed]")
            parts.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[vo]")
            parts.append("[vo][bed]amix=inputs=2:duration=first:normalize=0[a]")
            alabel = "[a]"
        else:
            alabel = "0:a"
        args = [*inputs]
        if parts:
            args += ["-filter_complex", ";".join(parts)]
        args += ["-map", vlabel, "-map", alabel]
        if total:
            args += ["-t", f"{total:.3f}"]
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-r", str(FPS),
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                 _abs_fwd(final)]
        return args

    attempts = [
        ("captions+music", True, True),
        ("music only (captions failed)", False, True),
        ("captions only (music failed)", True, False),
        ("bare concat", False, False),
    ]
    assembled = False
    for label, subs, mus in attempts:
        ok, err = run_ffmpeg(_final_args(subs, mus), cwd=workdir, timeout=1200)
        if ok and final.exists():
            if label != "captions+music":
                add_fallback(state, "cutter", f"final assembly degraded: {label} ({err[-120:]})")
            captions_kind = captions_kind if subs else "none"
            assembled = True
            break
        log("warn", f"final assembly '{label}' failed: {err[-160:]}")

    if not assembled:
        add_error(state, "cutter", "final assembly failed on all fallbacks")
        raise RuntimeError("assembly failed")

    progress(state, "cutter", f"video assembled -> {final.name} (captions={captions_kind})")
    return {"video_path": str(final), "captions_kind": captions_kind}
