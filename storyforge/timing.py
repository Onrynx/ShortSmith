"""Duration intelligence — how long a script will ACTUALLY be, and how to fix it.

The old system multiplied `target_seconds * fixed_wpm` and hoped. That is wrong
for three reasons, all of which this module addresses:

1. A TTS engine's real pace is not its nominal wpm. Kokoro at voice_speed 1.18
   does not deliver exactly 177 wpm — punctuation, sentence length and the voice
   itself move it. So we LEARN the real seconds-per-word from measured audio and
   apply that calibration to the next estimate (`load_calibration`/`record`).

2. Speech is not the only time in a video. Sentence-final pauses, scene
   transitions and dramatic beats all add seconds that a pure word count misses.
   `estimate_seconds` models those explicitly.

3. When a draft misses the target you should not delete random sentences (too
   long) or pad with filler (too short). `plan_revisions` picks the SPECIFIC
   scenes to tighten or develop and says by how much, so the rewrite is surgical
   and the story survives.
"""
from __future__ import annotations

import re
from pathlib import Path

from .utils import STATE_DIR, log, read_json, write_json

# --- one place for every timing constant (previously scattered as literals) --- #
BASE_WPM = 150.0          # Kokoro's nominal pace at speed 1.0
PAUSE_SENTENCE = 0.22     # breath after a sentence-ending . ! ?
PAUSE_CLAUSE = 0.07       # shorter beat after , ; : — (dash)
PAUSE_SCENE = 0.28        # gap between scenes (transition beat)
MIN_SCENE_SECONDS = 2.4   # a scene never holds for less than this on screen

# How close is "close enough" before we spend another model call fixing it.
TOLERANCE = 0.08          # ±8% of target
HARD_TOLERANCE = 0.15     # beyond this we always revise

_CALIB_FILE = STATE_DIR / "timing_calibration.json"
_CALIB_DEFAULT = 1.0
_CALIB_MIN, _CALIB_MAX = 0.70, 1.45   # refuse absurd drift from a bad sample


def word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", str(text or "").strip()) if w])


# --------------------------------------------------------------------------- #
# Calibration — learn the engine's real pace from audio we actually rendered
# --------------------------------------------------------------------------- #
def load_calibration(voice_style: str = "adam") -> float:
    """Measured seconds-per-word ratio vs. our model, per voice style."""
    data = read_json(_CALIB_FILE, {}) or {}
    entry = data.get(voice_style) or {}
    try:
        val = float(entry.get("factor", _CALIB_DEFAULT))
    except Exception:
        val = _CALIB_DEFAULT
    return min(_CALIB_MAX, max(_CALIB_MIN, val))


def record_calibration(voice_style: str, scenes: list, measured_total: float,
                       wpm: float) -> float:
    """Fold a real render's measured duration into the rolling calibration.

    Compares what we PREDICTED (at calibration 1.0) against what the TTS engine
    actually produced, then eases toward that ratio so one weird run can't
    whipsaw the estimate.
    """
    if not scenes or measured_total <= 0:
        return load_calibration(voice_style)
    predicted = estimate_seconds(scenes, wpm, calibration=1.0)
    if predicted <= 0:
        return load_calibration(voice_style)
    observed = measured_total / predicted
    data = read_json(_CALIB_FILE, {}) or {}
    entry = data.get(voice_style) or {}
    prev = float(entry.get("factor", _CALIB_DEFAULT) or _CALIB_DEFAULT)
    runs = int(entry.get("runs", 0) or 0)
    # exponential ease: fast to adapt at first, stable once we have history
    alpha = 0.5 if runs < 3 else 0.25
    factor = min(_CALIB_MAX, max(_CALIB_MIN, prev + alpha * (observed - prev)))
    data[voice_style] = {"factor": round(factor, 4), "runs": runs + 1,
                         "last_observed": round(observed, 4)}
    try:
        write_json(_CALIB_FILE, data)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not persist timing calibration: {exc}")
    log("info", f"timing calibration [{voice_style}]: {prev:.3f} -> {factor:.3f} "
                f"(this run measured {observed:.3f}x predicted)")
    return factor


# --------------------------------------------------------------------------- #
# Estimation — narration + pauses + transitions, not just words/wpm
# --------------------------------------------------------------------------- #
def scene_seconds(text: str, wpm: float, calibration: float = 1.0,
                  is_scene: bool = True) -> float:
    """Predicted spoken seconds for one scene, including its own pauses."""
    words = word_count(text)
    if not words:
        return MIN_SCENE_SECONDS if is_scene else 0.0
    speech = (words / max(wpm, 60.0)) * 60.0
    t = str(text)
    sentences = len(re.findall(r"[.!?]+", t))
    clauses = len(re.findall(r"[,;:—-]", t))
    pauses = sentences * PAUSE_SENTENCE + clauses * PAUSE_CLAUSE
    total = (speech + pauses) * calibration
    if is_scene:
        total += PAUSE_SCENE
        return max(MIN_SCENE_SECONDS, round(total, 2))
    return round(total, 2)


def estimate_seconds(scenes: list, wpm: float, calibration: float = 1.0) -> float:
    """Predicted runtime of a whole script (list of scenes or plain strings)."""
    total = 0.0
    for sc in scenes or []:
        text = sc.get("narration", "") if isinstance(sc, dict) else sc
        total += scene_seconds(text, wpm, calibration)
    return round(total, 2)


def words_for_seconds(seconds: float, wpm: float, calibration: float = 1.0,
                      scene_count: int = 1) -> int:
    """Invert the estimate: how many WORDS fill `seconds` of finished video.

    Subtracts the fixed overhead (scene transitions + sentence pauses) first, so
    the word budget is what's left for actual speech — the step the old
    `target/60*wpm` skipped entirely.
    """
    seconds = max(1.0, float(seconds))
    overhead = scene_count * PAUSE_SCENE
    # assume ~2 sentences per scene worth of breath
    overhead += scene_count * 2 * PAUSE_SENTENCE
    speech_seconds = max(1.0, (seconds / max(calibration, 0.1)) - overhead)
    return max(20, int(speech_seconds / 60.0 * max(wpm, 60.0)))


def wpm_for(voice_speed: float) -> float:
    return BASE_WPM * float(voice_speed or 1.0)


# --------------------------------------------------------------------------- #
# Fitting — decide IF and WHAT to revise (never blind truncation)
# --------------------------------------------------------------------------- #
def fit_status(actual: float, target: float, max_seconds: float | None = None) -> dict:
    """How well a runtime fits, and whether it's worth another model call."""
    target = max(1.0, float(target))
    delta = actual - target
    ratio = delta / target
    over_cap = bool(max_seconds and actual > float(max_seconds))
    if over_cap:
        action = "compress"
    elif abs(ratio) <= TOLERANCE:
        action = "ok"
    elif ratio > 0:
        action = "compress"
    else:
        action = "expand"
    return {
        "actual": round(actual, 2), "target": round(target, 2),
        "delta": round(delta, 2), "ratio": round(ratio, 4),
        "action": action, "over_cap": over_cap,
        "severe": abs(ratio) > HARD_TOLERANCE or over_cap,
    }


def _redundancy_score(text: str) -> float:
    """Rough 'how much fat is in here' — repeated words and filler phrases."""
    words = [w.lower().strip(".,!?;:") for w in str(text).split()]
    if not words:
        return 0.0
    repeats = 1.0 - (len(set(words)) / len(words))
    filler = sum(1 for f in ("basically", "actually", "literally", "just", "really",
                             "very", "kind of", "sort of", "at that point",
                             "in that moment", "to be honest")
                 if f in str(text).lower())
    return repeats + filler * 0.15


def plan_revisions(scenes: list, durations: list, target: float,
                   max_seconds: float | None = None,
                   protect: set | None = None) -> dict:
    """Pick WHICH scenes to tighten or develop, and by how much.

    Quality rule (the user's #6): never trim the load-bearing scenes. We protect
    the opening, the marked turn/reveal beats and the ending, then take time out
    of the flabbiest remaining scenes — or add it to the thinnest ones that are
    doing setup work.
    """
    n = len(scenes or [])
    if not n:
        return {"action": "ok", "targets": []}
    actual = sum(durations) if durations else 0.0
    status = fit_status(actual, target, max_seconds)
    if status["action"] == "ok":
        return {**status, "targets": []}

    protect = protect or set()
    # Always protect the hook and the final beat — they carry the whole video.
    protect = set(protect) | {0, n - 1}
    # Protect beats the planner marked as the turn/reveal.
    for i, sc in enumerate(scenes):
        if str(sc.get("purpose", "")).lower() in ("turn", "reveal", "resolve", "twist"):
            protect.add(i)

    need = abs(status["delta"])
    candidates = []
    for i, sc in enumerate(scenes):
        dur = durations[i] if i < len(durations) else 0.0
        text = sc.get("narration", "") if isinstance(sc, dict) else str(sc)
        if status["action"] == "compress":
            if i in protect:
                continue
            # prefer long, fatty, non-essential scenes
            candidates.append((i, _redundancy_score(text) * 4.0 + dur, dur, text))
        else:
            # expanding: favour scenes whose JOB is context/setup but are thin
            purpose = str(sc.get("purpose", "")).lower()
            bonus = 2.5 if purpose in ("establish", "context", "develop", "setup") else 0.0
            if i in protect and purpose not in ("establish", "context", "develop", "setup"):
                continue
            candidates.append((i, bonus + max(0.0, 8.0 - dur), dur, text))

    if not candidates:  # everything was protected — allow the least-critical ones
        candidates = [(i, durations[i] if i < len(durations) else 0.0,
                       durations[i] if i < len(durations) else 0.0,
                       scenes[i].get("narration", "")) for i in range(n) if i not in (0, n - 1)]
    candidates.sort(key=lambda c: c[1], reverse=True)

    targets, budget = [], need
    for i, _score, dur, text in candidates:
        if budget <= 0.4:
            break
        # never cut a scene below the on-screen floor, or by more than ~45%
        room = max(0.0, dur - MIN_SCENE_SECONDS) * 0.45 if status["action"] == "compress" else 6.0
        take = min(budget, room)
        if take < 0.5:
            continue
        targets.append({"index": i, "seconds": round(take, 2),
                        "current_seconds": round(dur, 2),
                        "current_words": word_count(text)})
        budget -= take
    return {**status, "targets": targets, "unresolved": round(max(0.0, budget), 2)}
