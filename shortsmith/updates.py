"""Update checking — is there a newer Shortsmith, and what changed?

Deliberately honest about two different installs:

* Cloned with git — updating is a pull, so the app can do it in one click.
* Downloaded as a ZIP (what the README tells most people to do) — there is no
  safe way to swap files under a running app, so we say what changed and send
  them to the download rather than pretending a button can do it.

The check is the only part of Shortsmith that talks to the internet without
being asked, it sends nothing but two plain GETs, and it never installs
anything on its own.

Knowing whether you are actually up to date is harder than it looks, and the
first version of this got it wrong in three ways:

* A ZIP install was never compared against anything, so it was told to
  "download the latest" forever — including immediately after doing exactly
  that. The answer is to publish a version number and compare it, which is why
  the remote CHANGELOG is fetched as well as the commit list.
* A git checkout was compared only against the last ten commits. One local
  commit of your own put HEAD outside that list and the app decided an update
  was waiting, permanently. Being AHEAD is now a state of its own.
* The version was read from the module that was imported at startup, so after
  a pull the app still reported the old one. It is now read from disk.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from . import __version__
from .utils import CREATE_NO_WINDOW, ROOT, log

REPO = "Onrynx/ShortSmith"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}/commits?per_page=10"
RAW_CHANGELOG = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/CHANGELOG.md"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
PAGE = f"https://github.com/{REPO}"

# Long enough that opening the Updates tab repeatedly does not hammer GitHub,
# short enough that a check an hour later is a real one.
CACHE_SECONDS = 900

_cache: dict = {}
_cache_at: float = 0.0


def is_git_checkout() -> bool:
    return (ROOT / ".git").is_dir()


def _git(*args, timeout: float = 25):
    kw = {"creationflags": CREATE_NO_WINDOW} if CREATE_NO_WINDOW else {}
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, timeout=timeout, **kw)


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #
_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([0-9][0-9A-Za-z.\-]*)["\']')
_HEADING_RE = re.compile(r"^##\s+v?([0-9][0-9A-Za-z.\-]*)\s*$", re.M)


def installed_version() -> str:
    """The version ON DISK, not the one imported when the app started.

    After a pull the files have changed but this process is still running the
    code it loaded at launch, so `__version__` would report the version we just
    replaced — and the app would keep offering the update it had already
    installed.
    """
    try:
        text = (ROOT / "shortsmith" / "__init__.py").read_text(encoding="utf-8")
        m = _VERSION_RE.search(text)
        if m:
            return m.group(1)
    except OSError:
        pass
    return __version__


def _vtuple(v: str) -> tuple:
    """Comparable form of a version string; unknown pieces sort low."""
    parts = []
    for chunk in re.split(r"[.\-]", str(v or "")):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, 0) if chunk else (0, 0))
    return tuple(parts)


def _newer(a: str, b: str) -> bool:
    """True when version a is strictly newer than b."""
    return _vtuple(a) > _vtuple(b)


def local_changelog(limit: int = 40) -> list[str]:
    path = ROOT / "CHANGELOG.md"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()[:limit]


def _fetch(url: str, timeout: float = 8) -> str:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Shortsmith"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _published_version() -> tuple[str | None, list[str]]:
    """The newest version named in the published CHANGELOG, and its notes."""
    try:
        text = _fetch(RAW_CHANGELOG)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not read the published changelog: {exc}")
        return None, []
    m = _HEADING_RE.search(text)
    if not m:
        return None, []
    # Everything from that heading up to the next one is what you would get.
    start = m.start()
    nxt = _HEADING_RE.search(text, m.end())
    notes = text[start:nxt.start() if nxt else len(text)].strip().splitlines()
    return m.group(1), notes[:40]


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #
def check(force: bool = False) -> dict:
    """What version we are on, and whether the published one has moved ahead."""
    global _cache_at
    if _cache and not force and (time.time() - _cache_at) < CACHE_SECONDS:
        return _cache

    version = installed_version()
    out = {
        "version": version,
        "latest_version": None,
        "git": is_git_checkout(),
        "state": "unknown",      # current | behind | ahead | unknown | error
        "up_to_date": False,
        "can_install": False,
        "behind": 0,
        "latest": None,
        "commits": [],
        "checked": True,
        "error": None,
        "zip_url": ZIP_URL,
        "page": PAGE,
        "changelog": local_changelog(),
        "whats_new": [],
    }

    try:
        data = json.loads(_fetch(API))
        out["commits"] = [{
            "sha": c.get("sha", "")[:7],
            "message": (c.get("commit", {}).get("message") or "").split("\n")[0],
            "when": (c.get("commit", {}).get("author", {}).get("date") or "")[:10],
        } for c in data][:10]
        out["latest"] = out["commits"][0]["sha"] if out["commits"] else None
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Could not reach GitHub ({exc})"
        out["state"] = "error"
        _cache.clear()
        return out

    # The published version number is what makes a ZIP install answerable at
    # all, and it is the tiebreaker when the commit history cannot decide.
    published, notes = _published_version()
    out["latest_version"] = published
    if published and _newer(published, version):
        out["whats_new"] = notes

    by_version = None
    if published:
        if _newer(published, version):
            by_version = "behind"
        elif _newer(version, published):
            by_version = "ahead"
        else:
            by_version = "current"

    if out["git"] and out["latest"]:
        try:
            head = _git("rev-parse", "HEAD").stdout.strip()[:7]
            out["head"] = head
            shas = [c["sha"] for c in out["commits"]]
            if head == out["latest"]:
                out["state"], out["behind"] = "current", 0
            elif head in shas:
                out["state"], out["behind"] = "behind", shas.index(head)
            else:
                # HEAD is not in the published history. Either this copy has
                # commits of its own (the normal state while developing) or it
                # is more than ten behind. The version number settles it.
                out["state"] = by_version or "unknown"
        except Exception:  # noqa: BLE001
            out["state"] = by_version or "unknown"
    else:
        out["state"] = by_version or "unknown"

    out["up_to_date"] = out["state"] in ("current", "ahead")
    out["can_install"] = out["git"] and out["state"] == "behind"
    _cache.clear()
    _cache.update(out)
    _cache_at = time.time()
    return out


def _remote_is_expected() -> tuple[bool, str]:
    """Is the branch we are about to pull actually tracking OUR repository?"""
    try:
        upstream = _git("rev-parse", "--abbrev-ref", "@{u}").stdout.strip()
        if not upstream or "/" not in upstream:
            return False, ("This branch is not tracking a remote, so there is "
                           "nothing to update from.")
        remote = upstream.split("/", 1)[0]
        url = _git("remote", "get-url", remote).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not read the git remote ({exc})."
    # Accept https and ssh spellings of the same repository, nothing else.
    wanted = REPO.lower()
    normal = url.lower().removesuffix(".git").replace(":", "/")
    if not normal.endswith("/" + wanted):
        return False, (f"This copy is set to pull from {url}, which is not "
                       f"{PAGE}. Updating was refused. Fix the git remote if "
                       f"that is unexpected.")
    return True, url


def _signature_state() -> str:
    """Whether the commit we just moved to carries a valid signature.

    Most repositories do not sign, so an unsigned commit is reported rather
    than treated as a failure — but the difference is worth recording, because
    "verified" and "we did not check" should never read the same.
    """
    try:
        r = _git("verify-commit", "HEAD", timeout=20)
    except Exception:  # noqa: BLE001
        return "signature not checked"
    if r.returncode == 0:
        return "signature verified"
    blob = ((r.stderr or "") + (r.stdout or "")).strip().lower()
    # git says NOTHING at all when a commit simply carries no signature — it
    # just exits non-zero. Reporting that as a failed check would read as an
    # alarm every single update on a repository that does not sign.
    if not blob or "no signature" in blob or "not signed" in blob:
        return "commit is unsigned"
    return "signature could NOT be verified"


def apply_update() -> dict:
    """Pull the newest code. Only possible on a git checkout."""
    global _cache_at
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

        # Pull executes whatever the remote hands back, so check WHICH remote
        # before doing it. `git pull` follows .git/config, and a config that
        # has been pointed somewhere else is the one way this button could
        # install code from a repository you never chose.
        ok, detail = _remote_is_expected()
        if not ok:
            return {"ok": False, "error": detail}

        pull = _git("pull", "--ff-only", timeout=90)
        if pull.returncode != 0:
            return {"ok": False, "error": (pull.stderr or pull.stdout).strip()[:300]}
        # Force the next check to look again, and report the version that is
        # now on disk rather than the one this process started with.
        _cache.clear()
        _cache_at = 0.0
        new_version = installed_version()
        signed = _signature_state()
        log("info", f"updated from GitHub (now v{new_version}, {signed})")
        return {"ok": True, "output": pull.stdout.strip()[:400],
                "version": new_version, "signature": signed, "restart": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


__all__ = ["check", "apply_update", "is_git_checkout", "installed_version",
           "local_changelog", "REPO", "PAGE", "ZIP_URL"]
