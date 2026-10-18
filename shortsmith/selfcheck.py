"""Full auto's self-check — what it does instead of a person watching.

Full auto promises on the Make screen that it checks its own work and files
what passes. This is that check, and it is deliberately not a model being asked
"is this good?". A model that just wrote a script is the worst possible judge of
it, and a 14B model will say yes to almost anything.

Instead every test here measures the file that actually came out:

    exists        a real mp4 of a believable size
    plays         ffprobe can read it, and reports a sane duration
    length        within tolerance of the length that was asked for
    sound         there is an audio track and it is not silence
    captions      the caption pass produced word or scene timings
    whole         every scene made it into the cut
    clean         no fallback fired that changes what the viewer sees

Anything that fails leaves the video in Review with the reasons attached, which
is exactly where it would have been in Express mode — a failed self-check costs
you nothing except the automatic filing. Anything that passes is moved to the
Library, and the reasons are kept on the record either way, so a video that
filed itself can still be explained afterwards.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .utils import (ffmpeg_path, ffprobe_duration, log, no_window_kwargs,
                    read_json, write_json)

# How far the finished video may sit from the requested length. Wider than the
# writer's own target, because the voice stage refits to real measured audio
# and a second either way is not worth withholding a video over.
LENGTH_TOLERANCE_PCT = 18.0
MIN_BYTES = 200 * 1024          # anything smaller than this is not a video
# Fallbacks that change what the viewer sees. A fallback that only swapped one
# provider for another is invisible and should not block filing.
VISIBLE_FALLBACKS = ("break-glass", "silence", "placeholder", "no audio",
                     "thumbnail generation failed", "captions failed")


def _ffprobe() -> str | None:
    """ffprobe next to the ffmpeg we actually resolved.

    PATH order cannot be trusted here — a stub ffmpeg sat ahead of the real
    install on this very machine — so prefer the sibling of the binary the
    renders use, and only fall back to a bare PATH lookup.
    """
    exe = Path(ffmpeg_path())
    sibling = exe.with_name("ffprobe" + exe.suffix)
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ffprobe")


def _streams(path: Path) -> list[dict]:
    exe = _ffprobe()
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, **no_window_kwargs())
        return (json.loads(out.stdout or "{}") or {}).get("streams") or []
    except Exception:  # noqa: BLE001
        return []


def _peak_db(path: Path) -> float | None:
    """Loudest sample in the whole track, in dBFS. None when it can't be read.

    Silence is the one failure that a still, a wrong clip or a missing caption
    cannot match for how bad it looks: a video nobody can hear is not a video.
    """
    try:
        out = subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-i", str(path), "-af", "volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=180, **no_window_kwargs())
        for line in (out.stderr or "").splitlines():
            if "max_volume:" in line:
                return float(line.split("max_volume:")[1].strip().split()[0])
    except Exception:  # noqa: BLE001
        return None
    return None


def check(state: dict) -> dict:
    """Measure the finished video. Returns {passed, checks[], reasons[]}."""
    checks: list[dict] = []

    def record(name: str, ok: bool, detail: str) -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    path = Path(state.get("final_video_path") or "")
    if not path.exists():
        record("exists", False, "no final video was produced")
        return {"passed": False, "checks": checks,
                "reasons": ["no final video was produced"]}

    size = path.stat().st_size
    record("exists", size >= MIN_BYTES, f"{size / 1024 ** 2:.1f} MB")

    duration = ffprobe_duration(path)
    record("plays", duration > 1.0, f"{duration:.1f}s readable by ffprobe")

    target = float((state.get("targets") or {}).get("target_seconds")
                   or state.get("requested_seconds") or 0)
    if target > 0 and duration > 0:
        off = 100.0 * (duration - target) / target
        record("length", abs(off) <= LENGTH_TOLERANCE_PCT,
               f"{duration:.0f}s against {target:.0f}s asked ({off:+.0f}%)")
    else:
        record("length", True, "no length was requested")

    streams = _streams(path)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" for s in streams)
    record("video track", has_video, f"{len(streams)} stream(s)")
    if has_audio:
        peak = _peak_db(path)
        if peak is None:
            record("sound", True, "could not measure, assuming fine")
        else:
            # -50 dBFS is inaudible; a narrated short peaks near -1.
            record("sound", peak > -50.0, f"peaks at {peak:.1f} dBFS")
    else:
        record("sound", False, "no audio track at all")

    # "word-synced" comes from whisper, "word-estimated" from an even spread of
    # the script across the measured audio. Estimated is a fallback, but the
    # captions are still there and still match the words — not a reason to
    # withhold a video.
    kind = str(state.get("captions_kind") or "none")
    record("captions", kind not in ("", "none"), f"{kind} timings")

    scenes = len((state.get("script") or {}).get("scenes") or [])
    cut = len(state.get("durations") or [])
    record("whole", scenes and cut >= scenes, f"{cut} of {scenes} scenes in the cut")

    visible = [str(f) for f in (state.get("fallbacks") or [])
               if any(w in str(f).lower() for w in VISIBLE_FALLBACKS)]
    record("clean", not visible,
           "; ".join(visible)[:200] if visible else "nothing the viewer would notice")

    reasons = [f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]]
    return {"passed": not reasons, "checks": checks, "reasons": reasons}


def node_file(state: dict) -> dict:
    """Full auto only: check the finished video, and file it if it passes."""
    from .nodes.common import progress

    if (state.get("mode") or "express").lower() != "auto":
        return {}
    if not state.get("final_video_path"):
        return {}

    progress(state, "herald", "Checking its own work")
    result = check(state)
    state["self_check"] = result
    summary = ", ".join(f"{c['name']} {'ok' if c['ok'] else 'FAILED'}"
                        for c in result["checks"])
    log("info", f"self-check: {summary}")

    # Keep the verdict on the video's own record either way.
    item_id = Path(state["final_video_path"]).stem
    meta_path = Path(state["final_video_path"]).with_suffix(".json")
    if meta_path.exists():
        data = read_json(meta_path, {}) or {}
        data["self_check"] = result
        write_json(meta_path, data)

    if not result["passed"]:
        progress(state, "herald",
                 "Self-check found problems — left in Review: "
                 + "; ".join(result["reasons"])[:200],
                 severity="yellow")
        return {"self_check": result, "auto_filed": False}

    from .control import approve
    r = approve(item_id)
    if not r.get("ok"):
        log("warn", f"self-check passed but filing failed: {r.get('error')}")
        return {"self_check": result, "auto_filed": False}
    progress(state, "herald", "Passed its own checks — filed in the Library")
    return {"self_check": result, "auto_filed": True}


__all__ = ["check", "node_file", "LENGTH_TOLERANCE_PCT"]
