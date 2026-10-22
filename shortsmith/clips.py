"""The background clips, as something you can see and manage.

Every video plays gameplay footage behind the narration, and until now those
clips were a folder you had to find on disk. The README told you to put files
in `assets/backgrounds/`, nothing in the app showed you what was there, and a
fresh install with an empty folder quietly fell through to a different kind of
visual without explaining why.

So: list them, add them, remove them, in the app.

Uploading is the part that needs care, because it is the only route in the
whole product that writes a file the user supplies:

  - the name is reduced to a bare, safe basename — never a path
  - the extension must be one the pipeline can actually play
  - the bytes stream to a .part file with a hard ceiling, so a wrong or
    hostile Content-Length cannot fill the disk
  - ffprobe has to find a real video stream before the file is kept
  - the final path is checked to still be inside the clips folder

A file that fails any of those is deleted rather than left half-written.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from .safety import inside
from .utils import ASSETS_DIR, STATE_DIR, ensure_dir, ffprobe_duration, log, run_ffmpeg

CLIPS_DIR = ensure_dir(ASSETS_DIR / "backgrounds")
THUMB_DIR = ensure_dir(STATE_DIR / "clipthumbs")

ALLOWED_EXT = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
MAX_CLIP_BYTES = 6 * 1024 ** 3        # 6 GB — a long 4K capture, and no more
# Deliberately low. ffprobe decides whether a file is a video; this only
# catches an empty or truncated transfer, and a floor set by guesswork would
# reject short clips that are perfectly valid.
MIN_CLIP_BYTES = 4 * 1024


def safe_name(raw: str | None) -> str | None:
    """A bare filename we are willing to create, or None.

    Only the basename survives: a browser can send "../../x.mp4" or a Windows
    path in the filename field, and the whole point of this function is that
    none of it reaches the filesystem.
    """
    if not raw:
        return None
    name = str(raw).replace("\\", "/").split("/")[-1].strip()
    if not name or name in (".", "..") or name.startswith("."):
        return None
    if len(name) > 150 or "\0" in name:
        return None
    stem, dot, ext = name.rpartition(".")
    if not dot or f".{ext.lower()}" not in ALLOWED_EXT:
        return None
    # Keep the user's own name where it is reasonable, drop what is not.
    cleaned = "".join(c for c in stem if c.isalnum() or c in " ._-()[]").strip()
    cleaned = cleaned.replace("  ", " ")[:100]
    if not cleaned:
        return None
    return f"{cleaned}.{ext.lower()}"


def _unique(name: str) -> Path:
    """A path that does not exist yet, so an upload never silently replaces."""
    dest = CLIPS_DIR / name
    if not dest.exists():
        return dest
    stem, _, ext = name.rpartition(".")
    for n in range(2, 500):
        candidate = CLIPS_DIR / f"{stem} ({n}).{ext}"
        if not candidate.exists():
            return candidate
    return CLIPS_DIR / f"{stem} {int(time.time())}.{ext}"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def _thumb_for(clip: Path) -> Path | None:
    """One frame from the middle of a clip, cached by name and mtime."""
    try:
        stamp = int(clip.stat().st_mtime)
    except OSError:
        return None
    out = THUMB_DIR / f"{clip.stem}_{stamp}.jpg"
    if out.exists() and out.stat().st_size > 512:
        return out
    duration = ffprobe_duration(clip)
    at = max(1.0, duration / 2) if duration else 1.0
    ok, _ = run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(clip.resolve()),
                        "-frames:v", "1", "-vf", "scale=-2:200",
                        str(out.resolve())], timeout=60)
    return out if ok and out.exists() else None


def listing(with_thumbs: bool = True) -> dict:
    """Every clip the pipeline can see, with what the screen needs to show it."""
    items = []
    total = 0
    if CLIPS_DIR.exists():
        for p in sorted(CLIPS_DIR.iterdir()):
            if not p.is_file() or p.suffix.lower() not in ALLOWED_EXT:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            total += size
            seconds = ffprobe_duration(p)
            items.append({
                "name": p.name,
                "size_mb": round(size / 1024 ** 2),
                "seconds": round(seconds) if seconds else None,
                "minutes": round(seconds / 60, 1) if seconds else None,
                "thumb": bool(_thumb_for(p)) if with_thumbs else False,
            })
    return {
        "clips": items,
        "count": len(items),
        "total_gb": round(total / 1024 ** 3, 2),
        "folder": str(CLIPS_DIR),
        # Below this the same few seconds get reused across scenes and the
        # background visibly loops, which is the usual "why does it repeat?".
        "enough": len(items) >= 1 and sum(i["seconds"] or 0 for i in items) >= 120,
    }


def thumb_path(name: str) -> Path | None:
    clip = clip_path(name)
    return _thumb_for(clip) if clip else None


def clip_path(name: str) -> Path | None:
    """The clip by name, or None if the name is not one we would have made."""
    safe = safe_name(name)
    if not safe:
        return None
    p = CLIPS_DIR / safe
    return p if p.is_file() and inside(p, CLIPS_DIR) else None


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def save_stream(raw_name: str, reader, declared: int) -> dict:
    """Stream an upload to disk, then prove it is a video before keeping it.

    `reader` is any object with .read(n). The bytes are never held in memory —
    a background clip is routinely gigabytes, and buffering one would be a
    denial of service against ourselves.
    """
    name = safe_name(raw_name)
    if not name:
        return {"ok": False, "error": (
            "That file type is not supported. Use mp4, mkv, webm, mov or m4v.")}
    if declared and declared > MAX_CLIP_BYTES:
        return {"ok": False, "error": (
            f"That file is larger than the {MAX_CLIP_BYTES // 1024 ** 3} GB limit.")}

    dest = _unique(name)
    if not inside(dest, CLIPS_DIR):
        return {"ok": False, "error": "that name cannot be used"}
    if dest.is_symlink():
        return {"ok": False, "error": "that name cannot be used"}

    # The scratch file gets a random name from mkstemp, which opens with
    # O_CREAT|O_EXCL: it fails rather than follows if anything is already at
    # that path. The old code wrote to a fully predictable "<name>.mp4.part",
    # and a symlink planted there in advance would have been followed — the
    # upload's bytes landing wherever the link pointed, outside this folder.
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".upload-", suffix=".part",
                                        dir=str(CLIPS_DIR))
    except OSError as exc:
        return {"ok": False, "error": f"could not start the copy ({exc})"}
    part = Path(tmp_name)

    written = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                if written >= declared:
                    break
                chunk = reader.read(min(1 << 20, declared - written))
                if not chunk:
                    break
                written += len(chunk)
                # The ceiling is enforced on what ARRIVES, not on what the
                # header claimed, so a lying Content-Length cannot get past it.
                if written > MAX_CLIP_BYTES:
                    raise IOError("upload exceeded the size limit")
                out.write(chunk)
    except Exception as exc:  # noqa: BLE001
        part.unlink(missing_ok=True)
        return {"ok": False, "error": f"the upload did not finish ({exc})"}

    # A short upload is a broken upload. Accepting one left a truncated file
    # that ffprobe might still open — a video missing its last seconds, which
    # would surface much later as a render that ends mid-sentence.
    if written != declared:
        part.unlink(missing_ok=True)
        return {"ok": False, "error": (
            f"the copy was cut short ({written:,} of {declared:,} bytes). "
            f"Try again.")}

    if written < MIN_CLIP_BYTES:
        part.unlink(missing_ok=True)
        return {"ok": False, "error": "that file is too small to be a video"}

    # Extension and size prove nothing. Ask the decoder.
    seconds = ffprobe_duration(part)
    if not seconds or seconds < 1.0:
        part.unlink(missing_ok=True)
        return {"ok": False, "error": (
            "That file could not be read as a video. If it plays elsewhere, "
            "try converting it to mp4 first.")}

    try:
        part.replace(dest)
    except OSError as exc:
        part.unlink(missing_ok=True)
        return {"ok": False, "error": f"could not save it ({exc})"}

    log("info", f"added background clip {dest.name} "
                f"({written / 1024 ** 2:.0f} MB, {seconds / 60:.1f} min)")
    _thumb_for(dest)
    return {"ok": True, "name": dest.name,
            "size_mb": round(written / 1024 ** 2),
            "minutes": round(seconds / 60, 1)}


def remove(name: str) -> dict:
    """Delete one clip, and the thumbnail cached for it."""
    clip = clip_path(name)
    if not clip:
        return {"ok": False, "error": "no such clip"}
    try:
        clip.unlink()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    for t in THUMB_DIR.glob(f"{clip.stem}_*.jpg"):
        try:
            t.unlink()
        except OSError:
            pass
    log("info", f"removed background clip {clip.name}")
    return {"ok": True, "removed": clip.name}


def free_space_gb() -> float:
    try:
        return round(shutil.disk_usage(CLIPS_DIR).free / 1024 ** 3, 1)
    except OSError:
        return 0.0


__all__ = ["listing", "save_stream", "remove", "clip_path", "thumb_path",
           "safe_name", "CLIPS_DIR", "ALLOWED_EXT", "MAX_CLIP_BYTES",
           "free_space_gb"]
