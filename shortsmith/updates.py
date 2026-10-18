"""Update checking — is there a newer Shortsmith, and what changed?

Deliberately honest about two different installs:

* Cloned with git — updating is a pull, so the app can do it in one click.
* Downloaded as a ZIP (what the README tells most people to do) — there is no
  safe way to swap files under a running app, so we say what changed and send
  them to the download rather than pretending a button can do it.

The check is the only part of Shortsmith that talks to the internet without
being asked, it sends nothing but a plain GET, and it never installs anything
on its own.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

from . import __version__
from .utils import CREATE_NO_WINDOW, ROOT, log

REPO = "Onrynx/ShortSmith"
API = f"https://api.github.com/repos/{REPO}/commits?per_page=10"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/main.zip"
PAGE = f"https://github.com/{REPO}"

_cache: dict = {}


def is_git_checkout() -> bool:
    return (ROOT / ".git").is_dir()


def _git(*args, timeout: float = 25):
    kw = {"creationflags": CREATE_NO_WINDOW} if CREATE_NO_WINDOW else {}
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, timeout=timeout, **kw)


def local_changelog(limit: int = 40) -> list[str]:
    path = ROOT / "CHANGELOG.md"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()[:limit]


def check(force: bool = False) -> dict:
    """What version we are on, and whether the repo has moved on."""
    if _cache and not force:
        return _cache

    out = {
        "version": __version__,
        "git": is_git_checkout(),
        "behind": 0,
        "latest": None,
        "commits": [],
        "checked": True,
        "error": None,
        "zip_url": ZIP_URL,
        "page": PAGE,
        "changelog": local_changelog(),
    }

    # What does the published repo look like right now?
    try:
        req = urllib.request.Request(API, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Shortsmith"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        out["commits"] = [{
            "sha": c.get("sha", "")[:7],
            "message": (c.get("commit", {}).get("message") or "").split("\n")[0],
            "when": (c.get("commit", {}).get("author", {}).get("date") or "")[:10],
        } for c in data][:10]
        out["latest"] = out["commits"][0]["sha"] if out["commits"] else None
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Could not reach GitHub ({exc})"
        _cache.clear()
        return out

    # How far behind is this copy? Only a git checkout can know precisely.
    if out["git"] and out["latest"]:
        try:
            head = _git("rev-parse", "HEAD").stdout.strip()[:7]
            out["head"] = head
            if head == out["latest"]:
                out["behind"] = 0
            else:
                shas = [c["sha"] for c in out["commits"]]
                out["behind"] = shas.index(head) if head in shas else -1
        except Exception:  # noqa: BLE001
            out["behind"] = -1
    _cache.clear()
    _cache.update(out)
    return out


def apply_update() -> dict:
    """Pull the newest code. Only possible on a git checkout."""
    if not is_git_checkout():
        return {"ok": False,
                "error": "This copy was downloaded as a ZIP, so it cannot "
                         "update itself. Download the new version and unzip "
                         "it over this folder."}
    try:
        dirty = _git("status", "--porcelain").stdout.strip()
        if dirty:
            return {"ok": False,
                    "error": "You have local changes here. Commit or discard "
                             "them first so an update cannot overwrite them."}
        pull = _git("pull", "--ff-only", timeout=90)
        if pull.returncode != 0:
            return {"ok": False, "error": (pull.stderr or pull.stdout).strip()[:300]}
        _cache.clear()
        log("info", "updated from GitHub")
        return {"ok": True, "output": pull.stdout.strip()[:400],
                "restart": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
