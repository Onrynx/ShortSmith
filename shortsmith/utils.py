"""Shared utilities for Shortsmith.

Deliberately stdlib-only (plus lazy Pillow/numpy inside functions). Importing this
module must never fail, so anything heavy is imported lazily where it is used.
All HTTP goes through urllib so the pipeline needs no `requests` dependency.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
import urllib.request
import urllib.parse
import urllib.error


# --------------------------------------------------------------------------- #
# Console encoding — Windows consoles default to cp1252 which can't encode the
# severity emoji (🔴🟡🟢) or ✅; force UTF-8 with replacement so logs never crash
# and never silently vanish. Runs once at import.
# --------------------------------------------------------------------------- #
def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_configure_stdio()


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"
MODELS_DIR = ROOT / "models"
ASSETS_DIR = ROOT / "assets"
STATE_DIR = ROOT / ".shortsmith_state"
STATUS_DIR = STATE_DIR / "status"

for _d in (OUTPUT_DIR, MODELS_DIR, ASSETS_DIR, STATE_DIR, STATUS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Tiny .env loader (so python-dotenv is optional)
# --------------------------------------------------------------------------- #
_ENV_LOADED = False


def load_env(path: Optional[Path] = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ (does not overwrite)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    path = path or (ROOT / ".env")
    try:
        if not path.exists():
            return
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception as exc:  # never let env parsing kill a run
        log("warn", f"could not parse .env: {exc}")


def env(key: str, default: Optional[str] = None) -> Optional[str]:
    load_env()
    val = os.environ.get(key, default)
    if val is not None and isinstance(val, str) and val.strip() == "":
        return default
    return val


def env_bool(key: str, default: bool = False) -> bool:
    val = env(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# Logging (severity aware, matches the notify() vocabulary)
# --------------------------------------------------------------------------- #
_SEV = {"info": "•", "ok": "🟢", "warn": "🟡", "error": "🔴", "debug": "·"}


def log(level: str, msg: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    icon = _SEV.get(level, "•")
    stream = sys.stderr if level in ("error", "warn") else sys.stdout
    try:
        print(f"[{stamp}] {icon} {msg}", file=stream, flush=True)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# JSON helpers (atomic writes)
# --------------------------------------------------------------------------- #
def read_json(path: Path | str, default: Any = None) -> Any:
    try:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log("warn", f"read_json {path}: {exc}")
        return default


def write_json(path: Path | str, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def append_jsonl(path: Path | str, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path | str, limit: Optional[int] = None) -> list:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    if limit:
        rows = rows[-limit:]
    return rows


# --------------------------------------------------------------------------- #
# Misc helpers
# --------------------------------------------------------------------------- #
def now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str, maxlen: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:maxlen].strip("-") or "untitled"


def ensure_dir(path: Path | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def word_count(text: str) -> int:
    return len((text or "").split())


def estimate_speech_seconds(text: str, wpm: int = 150) -> float:
    """Estimate spoken duration; floored so silent scenes still hold on screen."""
    words = word_count(text)
    secs = (words / max(wpm, 60)) * 60.0
    return max(2.4, round(secs + 0.4, 2))  # small trailing pad between scenes


# --------------------------------------------------------------------------- #
# Retry helper — the backbone of "retry 3x then fall back"
# --------------------------------------------------------------------------- #
def retry(fn: Callable, tries: int = 3, delay: float = 1.5, label: str = "op"):
    """Call fn() up to `tries` times. Returns fn() or raises the last error."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — deliberate broad catch
            last = exc
            log("warn", f"{label} attempt {attempt}/{tries} failed: {exc}")
            if attempt < tries:
                time.sleep(delay * attempt)
    raise last if last else RuntimeError(f"{label} failed")


# --------------------------------------------------------------------------- #
# HTTP via stdlib urllib (no `requests` dependency)
# --------------------------------------------------------------------------- #
def http_get_json(url: str, headers: dict | None = None, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict, headers: dict | None = None,
                   timeout: float = 120.0) -> Any:
    data = json.dumps(payload).encode("utf-8")
    hdr = {"Content-Type": "application/json"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        try:
            return json.loads(body)
        except Exception:
            return body


def http_download(url: str, dest: Path | str, headers: dict | None = None,
                  timeout: float = 120.0) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Shortsmith/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)
    if dest.stat().st_size == 0:
        raise IOError(f"downloaded empty file from {url}")
    return dest


def host_reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # server answered (even an error) => reachable
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Audio (WAV) helpers — stdlib wave, no ffprobe needed
# --------------------------------------------------------------------------- #
def wav_duration(path: Path | str) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 24000
            return round(frames / float(rate), 3)
    except Exception:
        return 0.0


def write_silence_wav(path: Path | str, seconds: float, rate: int = 24000,
                      tone_hz: float = 0.0, amplitude: float = 0.0) -> Path:
    """Write a mono 16-bit WAV. If tone_hz>0 writes a faint sine (audible placeholder),
    else near-silence. Used when a real TTS voice is unavailable."""
    import numpy as np  # lazy
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(seconds * rate))
    if tone_hz > 0 and amplitude > 0:
        t = np.arange(n) / rate
        # soft, slowly-tremolo'd tone so placeholder scenes are clearly "spoken-length"
        wave_data = amplitude * np.sin(2 * np.pi * tone_hz * t)
        wave_data *= (0.6 + 0.4 * np.sin(2 * np.pi * 0.6 * t))  # gentle amplitude wobble
        # fade in/out to avoid clicks
        fade = min(int(0.05 * rate), n // 2)
        if fade > 0:
            env = np.ones(n)
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
            wave_data *= env
        samples = (wave_data * 32767).astype("<i2")
    else:
        samples = np.zeros(n, dtype="<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return path


# --------------------------------------------------------------------------- #
# FFmpeg
# --------------------------------------------------------------------------- #
# Windows: keep child processes (ffmpeg/ffprobe/python) from flashing console
# windows over whatever you're doing. A render fires dozens of ffmpeg calls, so
# without this the desktop strobes for minutes.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def no_window_kwargs() -> dict:
    """subprocess kwargs that suppress console windows on Windows (no-op elsewhere)."""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # hide even if a console appears
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": si}


_FFMPEG_CACHE: list = []


def _is_real_ffmpeg(path: str) -> bool:
    """Does this binary behave like ffmpeg, or is it a stub wearing the name?"""
    try:
        # Probe with the exact flags every render uses. A stub answered plain
        # "-version" convincingly but choked on -hide_banner, so testing the
        # softer command would have validated the broken binary.
        out = subprocess.run([path, "-hide_banner", "-version"],
                             capture_output=True, text=True, timeout=12,
                             **no_window_kwargs())
        blob = ((out.stdout or "") + (out.stderr or "")).lower()
        return out.returncode == 0 and "ffmpeg version" in blob
    except Exception:  # noqa: BLE001
        return False


def ffmpeg_path() -> str:
    """Find an ffmpeg that actually works.

    `shutil.which` returns the FIRST match on PATH, which is not necessarily a
    real ffmpeg. On a live machine Python's Scripts directory sat ahead of the
    real install and provided an `ffmpeg.EXE` stub that rejects -hide_banner,
    so every single render failed its visuals with "Unrecognized option" while
    a working ffmpeg 8.1.1 sat further down the same PATH. So: take the first
    candidate that answers `-version` like ffmpeg, and remember it.
    """
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE[0]
    override = (env("FFMPEG_BIN", "") or "").strip()
    candidates = ([override] if override else []) + _which_all("ffmpeg")
    for cand in candidates:
        if cand and _is_real_ffmpeg(cand):
            if candidates.index(cand) > 0:
                log("info", f"using ffmpeg at {cand} (earlier PATH entries were not ffmpeg)")
            _FFMPEG_CACHE.append(cand)
            return cand
    fallback = shutil.which("ffmpeg") or "ffmpeg"
    _FFMPEG_CACHE.append(fallback)
    return fallback


def _which_all(name: str) -> list:
    """Every match for `name` on PATH, in order."""
    found, seen = [], set()
    for d in (os.environ.get("PATH") or "").split(os.pathsep):
        if not d:
            continue
        hit = shutil.which(name, path=d)
        if hit and hit.lower() not in seen:
            seen.add(hit.lower())
            found.append(hit)
    return found


def has_ffmpeg() -> bool:
    return _is_real_ffmpeg(ffmpeg_path())


def run_ffmpeg(args: list[str], cwd: Optional[Path | str] = None,
               timeout: float = 900.0) -> tuple[bool, str]:
    """Run ffmpeg with -y -hide_banner. Returns (ok, stderr_tail).

    cwd is important on Windows: running with cwd set to the workdir lets us pass
    *relative* subtitle/asset filenames and dodge the drive-letter colon that the
    ffmpeg `subtitles` filter otherwise mis-parses (C:\\ -> filter option split).
    """
    cmd = [ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", *args]
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
            **no_window_kwargs(),
        )
        ok = proc.returncode == 0
        tail = (proc.stderr or "").strip()
        if not ok:
            log("warn", f"ffmpeg rc={proc.returncode}: {tail[-500:]}")
        return ok, tail
    except FileNotFoundError:
        return False, "ffmpeg not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def ffprobe_duration(path: Path | str) -> float:
    """Best-effort media duration via ffprobe (falls back to 0)."""
    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        return float((out.stdout or "0").strip() or 0.0)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# Status files (read by the Discord bot for live per-lane progress)
# --------------------------------------------------------------------------- #
_SAFE_LANE = re.compile(r"[^A-Za-z0-9_.-]")


def write_status(lane: str, data: dict) -> None:
    # A lane name becomes a filename. progress() defaults it to "?", which is
    # not a legal character on Windows — writing it raises OSError and takes
    # the whole render down over a status update nobody reads.
    safe = _SAFE_LANE.sub("_", str(lane or "render")) or "render"
    data = {**data, "updated": utcnow_iso()}
    write_json(STATUS_DIR / f"{safe}.json", data)


def read_status(lane: str) -> dict:
    return read_json(STATUS_DIR / f"{lane}.json", {}) or {}
