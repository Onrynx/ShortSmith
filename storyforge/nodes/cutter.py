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
from ..utils import (ASSETS_DIR, ensure_dir, env, has_ffmpeg, log, run_ffmpeg)
from .common import add_error, add_fallback, progress

FPS = 30
MUSIC_PATH = ASSETS_DIR / "ambient_bed.wav"


# --------------------------------------------------------------------------- #
# Ambient music bed (synthesized CC0 fallback if the shipped asset is missing)
# --------------------------------------------------------------------------- #
MUSIC_DIR = ASSETS_DIR / "music"
_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")

# mood -> (keywords scored against title/hook/tags, synth chord for the default bed)
_MOODS = {
    "mystery":   (["mystery", "vanish", "lost", "unsolved", "secret", "disappear", "clue"],
                  [110.0, 130.81, 164.81, 196.0]),
    "epic":      (["king", "war", "battle", "legend", "empire", "throne", "rise", "fall"],
                  [73.42, 110.0, 146.83, 220.0]),
    "dark":      (["curse", "death", "haunt", "ghost", "shadow", "forbidden", "doom"],
                  [55.0, 65.41, 82.41, 98.0]),
    "adventure": (["journey", "quest", "explore", "treasure", "discover", "expedition"],
                  [98.0, 123.47, 146.83, 196.0]),
    "chill":     (["calm", "peace", "gentle", "quiet"],
                  [130.81, 164.81, 196.0, 246.94]),
}
_CHANNEL_DEFAULT_MOOD = {"history_mysteries": "mystery", "gaming_lore": "epic",
                         "gossip_stories": "chill"}


def pick_music(state: dict) -> tuple[Path | None, str]:
    """Choose a background track matched to the story's mood.

    Looks in assets/music/<mood>/ first (drop your no-copyright tracks there),
    then anywhere under assets/music/, else synthesizes a CC0 mood bed."""
    import random
    script = state.get("script", {}) or {}
    text = " ".join([script.get("title", ""), script.get("hook", ""),
                     " ".join(script.get("tags", []))]).lower()
    scores = {m: sum(k in text for k in keys) for m, (keys, _) in _MOODS.items()}
    best = max(scores.values() or [0])
    mood = (max(scores, key=scores.get) if best > 0
            else _CHANNEL_DEFAULT_MOOD.get(state.get("channel", ""), "mystery"))

    mood_dir = MUSIC_DIR / mood
    tracks = [p for p in mood_dir.glob("*") if p.suffix.lower() in _AUDIO_EXTS] \
        if mood_dir.exists() else []
    if not tracks:  # any user track at all?
        tracks = [p for p in MUSIC_DIR.rglob("*") if p.suffix.lower() in _AUDIO_EXTS]
        tracks = [p for p in tracks if not p.name.startswith("default_")] or tracks
    if tracks:
        return random.choice(tracks), mood
    # synthesize a mood-specific CC0 bed once, cache it in the mood folder
    bed = mood_dir / f"default_{mood}.wav"
    try:
        if not bed.exists():
            synth_ambient_bed(bed, chord=_MOODS[mood][1])
        return bed, mood
    except Exception as exc:  # noqa: BLE001
        log("warn", f"mood bed synth failed: {exc}")
        return (_ensure_music(), mood)


def synth_ambient_bed(path: Path, seconds: int = 70, rate: int = 44100,
                      chord: list | None = None) -> Path:
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * rate)) / rate
    chord = chord or [110.0, 164.81, 220.0, 277.18]  # A minor-ish pad
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


def _scene_cues(scenes, spoken, slots=None, chunk=8):
    """Split narration into ~`chunk`-word cues. `spoken` = speech seconds per
    scene (cues span these); `slots` = full slot length per scene on the timeline
    (>= spoken when a scene is extended to hit an exact runtime) — captions stop
    when the speech stops instead of lingering over the outro."""
    slots = slots or spoken
    cues = []
    t = 0.0
    for i, sc in enumerate(scenes):
        dur = spoken[i] if i < len(spoken) else 4.0
        words = (sc.get("narration") or "").split()
        chunks = [" ".join(words[j:j + chunk]) for j in range(0, len(words), chunk)] or [sc.get("narration", "")]
        total_w = sum(len(c.split()) for c in chunks) or 1
        ct = t
        for c in chunks:
            d = dur * (len(c.split()) / total_w)
            cues.append((ct, ct + max(0.4, d), c))
            ct += d
        t += slots[i] if i < len(slots) else dur
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
# Viral-style captions (ASS): extra-bold uppercase word-pop, white + black
# outline, slightly below center — the Hormozi/MrBeast-clip look.
# --------------------------------------------------------------------------- #
def _words_whisper(audio_paths, factor=1.0):
    """True word timings from the narration via faster-whisper. None on failure."""
    try:
        import numpy as np
        from faster_whisper import WhisperModel  # type: ignore
        pcm, rate = [], 24000
        for p in audio_paths:
            with wave.open(p, "rb") as wf:
                rate = wf.getframerate()
                pcm.append(np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2"))
        if not pcm:
            return None
        audio = np.concatenate(pcm).astype("float32") / 32768.0
        if rate != 16000:  # whisper wants 16k
            n = int(len(audio) * 16000 / rate)
            audio = np.interp(np.linspace(0, len(audio), n, endpoint=False),
                              np.arange(len(audio)), audio).astype("float32")
        model = WhisperModel(os.environ.get("WHISPER_MODEL", "tiny"),
                             device="cpu", compute_type="int8")
        segs, _ = model.transcribe(audio, word_timestamps=True, language="en")
        words = [(wd.start / factor, wd.end / factor, wd.word.strip())
                 for s in segs for wd in (s.words or []) if wd.word.strip()]
        return words or None
    except Exception as exc:  # noqa: BLE001
        log("warn", f"whisper word-timing failed: {exc}")
        return None


def _words_estimate(scenes, spoken, slots):
    """Fallback word timings: distribute each scene's words across its speech."""
    words = []
    t = 0.0
    for i, sc in enumerate(scenes):
        toks = (sc.get("narration") or "").split()
        dur = spoken[i] if i < len(spoken) else 4.0
        weights = [len(w) + 2 for w in toks]
        total = sum(weights) or 1
        wt = t
        for w, wgt in zip(toks, weights):
            d = dur * wgt / total
            words.append((wt, wt + d, w))
            wt += d
        t += slots[i] if i < len(slots) else dur
    return words


def _ass_ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600); m = int(sec % 3600 // 60); s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _words_to_ass(words, w, h) -> str:
    """Group words into <=3-word cues and render pop-in ASS events."""
    fs = int(h * 0.046)
    posy = int(h * 0.655)          # a bit below center, clear of UI overlays
    outline = max(3, int(h * 0.0045))
    # Cross-platform: "Arial Black" on Win/macOS; set CAPTION_FONT on Linux
    # (e.g. "DejaVu Sans" / "Impact"). libass falls back if the name is missing.
    font = env("CAPTION_FONT", "Arial Black") or "Arial Black"
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H80000000,-1,0,0,0,100,100,1,0,1,{outline},2,5,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    # Fast, punchy pops: <=2 words, break on small gaps or a short span. Matches
    # the sped-up narration so captions never lag behind the voice.
    cues, cur = [], []
    for wd in words:
        if cur and (len(cur) == 2 or wd[0] - cur[-1][1] > 0.35 or wd[1] - cur[0][0] > 0.9):
            cues.append(cur)
            cur = []
        cur.append(wd)
    if cur:
        cues.append(cur)
    ev = []
    for n, cue in enumerate(cues):
        start, end = cue[0][0], max(cue[-1][1], cue[0][0] + 0.35)
        text = " ".join(c[2] for c in cue).upper().replace("{", "").replace("}", "")
        color = r"\c&H00FFFF&" if (n % 5 == 4) else ""   # every 5th cue pops yellow
        fx = (rf"{{\pos({w // 2},{posy})\fscx70\fscy70"
              rf"\t(0,80,\fscx100\fscy100){color}}}")
        ev.append(f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Cap,,0,0,0,,{fx}{text}")
    return head + "\n".join(ev) + "\n"


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
    # apad: silence-fill audio to the slot length (keeps A/V sync in concat and
    # lets an extended last scene breathe over background + music)
    afilter = f"[1:a]atempo={max(0.5, min(2.0, atempo)):.4f},apad[a]"
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
    exact = spec.get("exact_seconds") or 0
    # An explicit "make it 45 seconds" request outranks the channel's window.
    req = state.get("requested_seconds")
    cap = exact or (float(req) * 1.15 if req else max_seconds)

    # Time-fit of LAST resort. The script stage plans to the target and the voice
    # stage revises against measured audio, so by here we should already be close.
    # Speeding narration up is audible — it makes the narrator sound rushed — so
    # it is capped hard. If we're still far over, the honest outcome is a slightly
    # longer video, not a chipmunk.
    total_raw = sum(durations) if durations else 0.0
    factor = 1.0
    if total_raw > cap and total_raw > 0:
        factor = min(1.12, round(total_raw / cap, 3))
        if total_raw / cap > 1.12:
            add_fallback(state, "cutter",
                         f"runtime {total_raw:.0f}s over {cap:.0f}s target; nudging "
                         f"{factor:.2f}x (kept natural rather than over-speeding)")
    # Hard backstop: a Short MUST stay under YouTube's limit, so if the gentle
    # nudge isn't enough we do speed up further — correctness beats prettiness.
    if spec["format"] == "shorts" and total_raw / factor > max_seconds:
        factor = min(1.6, round(total_raw / max_seconds, 3))
        add_fallback(state, "cutter",
                     f"runtime would exceed the {max_seconds:.0f}s Shorts limit; "
                     f"speeding {factor:.2f}x to stay compliant")
    spoken = [round(d / factor, 3) for d in durations]   # speech per scene
    edur = list(spoken)                                  # slot per scene
    # ...and if we're under an EXACT runtime, extend the last scene: background
    # keeps playing, narration apads to silence, music carries the outro.
    if exact and edur and sum(edur) < exact:
        edur[-1] = round(edur[-1] + (exact - sum(edur)), 3)

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
    # Viral word-pop captions, truly synced to the voice via whisper word
    # timestamps (estimated distribution as fallback).
    words = None
    if not state.get("mock"):
        words = _words_whisper(audio_paths, factor)
        captions_kind = "word-synced"
    if not words:
        words = _words_estimate(scenes, spoken, edur)
        captions_kind = "word-estimated"
    (workdir / "captions.ass").write_text(_words_to_ass(words, w, h), encoding="utf-8")

    # 4) final assembly — mood-matched music + captions, graceful degradation
    music, mood = pick_music(state)
    if music:
        progress(state, "cutter", f"music: {music.name} (mood={mood})")
        state["music_track"] = f"{music.name} ({mood})"
    total = round(sum(edur), 3) if edur else None
    final = workdir / f"{state['run_id']}.mp4"

    sub_filter = "ass=captions.ass"

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
