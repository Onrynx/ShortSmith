"""Find older copies of Shortsmith on this computer, and replace them.

Someone whose copy will not update -- too old to have a working update
button, or stuck on a screen that covers it -- downloads Shortsmith again.
Without help they end up with two copies: the new one, and the old one still
holding their videos and their gameplay footage. This finds the old one,
moves their things into this copy, and removes it.

What counts as an older copy is deliberately narrow, because the last step
deletes a folder:

  * It has the layout a download from the page unpacks to: shortsmith/ with
    a version number, app/index.html, run.py, the bundled runtime/ folder
    and one of the launchers. Nothing else on a computer looks like that.
  * Its version is OLDER than this one. A newer copy is left alone.
  * It is not inside a git checkout. A developer's working copy, and
    anything built inside one, is never touched.
  * It is not this copy, and does not contain or sit inside this copy.
  * It is a real folder, reached without following links or junctions.

The search looks only where people put downloads -- Desktop, Downloads,
Documents, and the folder this copy was unpacked into -- a few levels down.

Nothing is deleted until the person says so, and never before their videos,
footage and story memory have been moved into this copy. If moving fails,
the old copy is left exactly where it is.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import threading
import time
from pathlib import Path

from . import utils as _u
from .utils import ROOT, log, read_json, write_json

LAUNCHERS = ("Shortsmith.vbs", "Shortsmith.command", "Shortsmith.sh")
CLIP_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([0-9][0-9A-Za-z.\-]*)["\']')
_SKIP = {"node_modules", "AppData", "Library", "$Recycle.Bin", "Windows",
         "Program Files", "Program Files (x86)", "ProgramData", "site-packages",
         "__pycache__", "OneDriveTemp"}
MAX_DIRS = 20000                       # a ceiling on how long a search can take

_lock = threading.Lock()
_state: dict = {"scanning": False, "copies": [], "scanned_at": None}
# Copies whose removal has started. One that could only be partly removed no
# longer has the full layout, and a second press has to be allowed to finish.
_partial: set = set()


def _dismissed_file() -> Path:
    return _u.STATE_DIR / "old_copies.json"


# --------------------------------------------------------------------------- #
# What an older copy is
# --------------------------------------------------------------------------- #
def _version_of(folder: Path) -> str | None:
    try:
        init = folder / "shortsmith" / "__init__.py"
        if not init.is_file():
            return None
        if not ((folder / "app" / "index.html").is_file()
                and (folder / "run.py").is_file()
                and (folder / "runtime").is_dir()):
            return None
        if not any((folder / n).is_file() for n in LAUNCHERS):
            return None
        m = _VERSION_RE.search(init.read_text(encoding="utf-8", errors="replace")[:20000])
        return m.group(1) if m else None
    except OSError:
        return None


def _in_git(folder: Path) -> bool:
    for d in (folder, *folder.parents):
        if (d / ".git").exists():
            return True
    return False


def _related(a: Path, b: Path) -> bool:
    """The same folder, or one inside the other."""
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def _older(v: str, than: str) -> bool:
    from .updates import _newer
    return _newer(than, v)


def qualifies(folder: Path) -> str | None:
    """Its version if it is an older Shortsmith copy that may be replaced."""
    from . import __version__
    try:
        if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
            return None
        version = _version_of(folder)
        if (not version or not _older(version, __version__) or _in_git(folder)
                or _related(folder, ROOT)):
            return None
        # A folder this short is a drive or a home folder, not a download.
        if len(folder.resolve().parts) < 3 or folder.resolve() == Path.home().resolve():
            return None
    except OSError:
        return None
    return version


# --------------------------------------------------------------------------- #
# Where to look
# --------------------------------------------------------------------------- #
def _roots() -> list[tuple[Path, int]]:
    """(folder, how many levels down to look)."""
    override = os.environ.get("SHORTSMITH_SCAN_ROOTS", "").strip()
    if override:
        return [(Path(p), 3) for p in override.split(os.pathsep) if p]
    home = Path.home()
    roots = [(ROOT.parent, 2), (home / "Desktop", 3), (home / "Downloads", 3)]
    if sys.platform == "darwin":
        # Not Documents or the home folder: on a Mac, looking inside those
        # makes macOS ask for permission, for a search the person never saw.
        roots += [(home / "Applications", 1), (Path("/Applications"), 1)]
    else:
        roots += [(home / "Documents", 3), (home, 1)]
        if os.name == "nt" and os.environ.get("OneDrive"):
            od = Path(os.environ["OneDrive"])
            roots += [(od / "Desktop", 3), (od / "Documents", 3)]
    return roots


def _walk(root: Path, depth: int, seen: dict, budget: list, found: dict) -> None:
    # The search areas overlap -- this copy's own folder is often the Desktop
    # -- so a folder already searched is searched again only when this route
    # allows it to go deeper. Marking it "done" outright let the shallower
    # search win and hid a copy two folders down.
    stack = [(root, 0)]
    while stack and budget[0] > 0:
        d, lvl = stack.pop()
        key = os.path.normcase(os.path.abspath(str(d)))
        if seen.get(key, -1) >= depth - lvl:
            continue
        seen[key] = depth - lvl
        budget[0] -= 1
        if _version_of(d):
            found[key] = d
            continue                    # never look inside a copy for more copies
        if lvl >= depth:
            continue
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if (not e.is_dir(follow_symlinks=False) or e.is_symlink()
                        or getattr(e, "is_junction", lambda: False)()):
                    continue
            except OSError:
                continue
            if e.name.startswith(".") or e.name in _SKIP:
                continue
            stack.append((Path(e.path), lvl + 1))


def _inventory(folder: Path) -> dict:
    """What would be moved across: the person's videos and footage."""
    videos, clips, clip_bytes = 0, 0, 0
    for f in (folder / "output").glob("*/*/*.mp4"):
        if f.parent.name in ("approved", "pending"):
            videos += 1
    bg = folder / "assets" / "backgrounds"
    if bg.is_dir():
        for f in bg.iterdir():
            if f.is_file() and f.suffix.lower() in CLIP_EXTS:
                clips += 1
                try:
                    clip_bytes += f.stat().st_size
                except OSError:
                    pass
    return {"videos": videos, "clips": clips, "clips_gb": round(clip_bytes / 1024 ** 3, 1)}


def _running_from(folder: Path) -> bool:
    """Is anything running out of that folder -- the old copy, still open?"""
    try:
        import psutil  # type: ignore
    except ImportError:
        return False
    inside = os.path.normcase(str(folder.resolve())) + os.sep
    for p in psutil.process_iter(["exe", "cmdline"]):
        try:
            parts = [p.info.get("exe") or ""] + list(p.info.get("cmdline") or [])
            if any(os.path.normcase(os.path.abspath(x)).startswith(inside)
                   for x in parts if x and (os.sep in x or "/" in x)):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def scan() -> dict:
    """Look for older copies now. Safe to call from a thread."""
    with _lock:
        if _state["scanning"]:
            return dict(_state)
        _state["scanning"] = True
    t0, seen, budget, found = time.time(), {}, [MAX_DIRS], {}
    try:
        for root, depth in _roots():
            if root.is_dir():
                _walk(root, depth, seen, budget, found)
        copies = []
        for folder in found.values():
            version = qualifies(folder)
            if version:
                copies.append({"path": str(folder), "version": version,
                               **_inventory(folder)})
    finally:
        with _lock:
            _state["scanning"] = False
    with _lock:
        _state.update(copies=copies, scanned_at=time.time())
    if copies:
        log("info", f"found {len(copies)} older Shortsmith cop(ies) in "
                    f"{time.time() - t0:.1f}s: {', '.join(c['path'] for c in copies)}")
    return state()


def start_scan(delay: float = 0.0) -> None:
    def go():
        if delay:
            time.sleep(delay)
        try:
            scan()
        except Exception as exc:  # noqa: BLE001
            log("warn", f"looking for older copies failed: {exc}")
    threading.Thread(target=go, daemon=True).start()


def state() -> dict:
    dismissed = set((read_json(_dismissed_file(), {}) or {}).get("dismissed", []))
    with _lock:
        copies = [dict(c, dismissed=c["path"] in dismissed) for c in _state["copies"]]
        return {"scanning": _state["scanning"], "scanned": _state["scanned_at"] is not None,
                "copies": copies}


def dismiss(path: str) -> dict:
    data = read_json(_dismissed_file(), {}) or {}
    data["dismissed"] = sorted(set(data.get("dismissed", [])) | {str(path)})
    write_json(_dismissed_file(), data)
    return state()


# --------------------------------------------------------------------------- #
# Replacing one
# --------------------------------------------------------------------------- #
def _move_into(src_dir: Path, dst_dir: Path, only=None) -> list:
    """Move files across; a file already there with the same size is kept."""
    if not src_dir.is_dir():
        return []
    moved = []
    for f in src_dir.iterdir():
        if not f.is_file() or (only and not only(f)):
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f.name
        if dst.exists():
            if dst.stat().st_size == f.stat().st_size:
                continue
            dst = dst_dir / f"{f.stem} (from the old copy){f.suffix}"
        shutil.move(str(f), str(dst))
        moved.append(dst)
    return moved


def _bring_over(old: Path) -> dict:
    videos = 0
    for ch in (old / "output").glob("*"):
        if ch.is_dir() and not ch.name.startswith("_"):
            for where in ("approved", "pending"):
                # Every file of each video moves; only the videos are counted.
                moved = _move_into(ch / where, _u.OUTPUT_DIR / ch.name / where)
                videos += sum(1 for f in moved if f.suffix.lower() == ".mp4")
    clip_bytes = sum(f.stat().st_size for f in (old / "assets" / "backgrounds").glob("*")
                     if f.is_file() and f.suffix.lower() in CLIP_EXTS) \
        if (old / "assets" / "backgrounds").is_dir() else 0
    clips = len(_move_into(old / "assets" / "backgrounds", _u.ASSETS_DIR / "backgrounds",
                           only=lambda f: f.suffix.lower() in CLIP_EXTS))
    memory = old / ".shortsmith_state" / "recent_stories.json"
    mine = _u.STATE_DIR / "recent_stories.json"
    if memory.is_file() and not mine.exists():
        shutil.copy2(memory, mine)       # so it does not repeat old stories
    return {"videos": videos, "clips": clips, "clips_gb": round(clip_bytes / 1024 ** 3, 1)}


def _remove_tree(folder: Path) -> list[str]:
    """Delete the folder; read-only files are made writable first."""
    stuck: list[str] = []

    def retry(func, path, *_):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:  # noqa: BLE001
            stuck.append(str(path))

    if sys.version_info >= (3, 12):
        shutil.rmtree(folder, onexc=retry)
    else:
        shutil.rmtree(folder, onerror=retry)
    return stuck


def replace(path: str) -> dict:
    """Move the person's things out of an older copy, then remove it."""
    with _lock:
        known = any(c["path"] == path for c in _state["copies"])
    if not known:
        # Only ever a folder the search itself found -- never a path that
        # simply arrived in a request.
        return {"ok": False, "error": "That is not an older copy Shortsmith found. "
                                      "Press Look again."}
    folder = Path(path)
    resuming = path in _partial
    if resuming and not folder.exists():
        _partial.discard(path)
        with _lock:
            _state["copies"] = [c for c in _state["copies"] if c["path"] != path]
        return {"ok": True, "removed": path, "videos": 0, "clips": 0, "clips_gb": 0}
    if not resuming and not qualifies(folder):
        return {"ok": False, "error": "That folder no longer looks like an older "
                                      "Shortsmith, so it was left alone."}
    if _running_from(folder):
        return {"ok": False, "error": "That older copy is open right now. Close it, "
                                      "then press Remove again."}
    try:
        moved = _bring_over(folder)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not move things out of {folder}: {exc}")
        return {"ok": False, "error": f"Could not move your videos and clips across "
                                      f"({exc}). The old copy was left as it is."}
    # Checked once more, immediately before deleting: the same narrow test.
    if not resuming and not qualifies(folder):
        return {"ok": False, "error": "That folder changed while your things were "
                                      "being moved, so it was left alone.", **moved}
    _partial.add(path)
    stuck = _remove_tree(folder)
    log("info", f"replaced older copy {folder}: moved {moved['videos']} video file(s) "
                f"and {moved['clips']} clip(s); {len(stuck)} file(s) could not be removed")
    if stuck:
        return {"ok": False, **moved, "error": (
            "Your videos and clips were moved here, but some of the old copy could "
            "not be removed because something is using it. Close it and press "
            "Remove again.")}
    _partial.discard(path)
    with _lock:
        _state["copies"] = [c for c in _state["copies"] if c["path"] != path]
    return {"ok": True, "removed": path, **moved}
