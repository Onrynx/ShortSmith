#!/usr/bin/env python
"""Write manifest.json — the list of files an install is made of.

Run this before publishing a release. The updater on each machine fetches this
file, hashes what it has locally, and downloads only the entries that differ.
That is what makes an update a few kilobytes instead of a whole new copy.

    python scripts/make_manifest.py

What is NOT in the manifest is as important as what is. Anything the user owns
or generates — their videos, their clips, their models, their .env, the state
directory — is excluded, so an update can never reach it.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories that belong to the user or are rebuilt locally.
SKIP_DIRS = {
    ".git", ".github", ".claude", "__pycache__", ".venv", "venv", "env",
    ".idea", ".vscode",
    "output",            # finished and pending videos
    "models",            # multi-gigabyte weights, fetched separately
    ".shortsmith_state", # runtime state, logs, caches
    "build",             # the bundled runtime this script helps produce
    "docs",              # the download page; GitHub Pages serves it, installs do not
    "scripts",           # build tooling, not part of anyone's install
    "mockup",
}
# The manifest describes what an INSTALL consists of. Anything only a person
# working on Shortsmith needs is left out, so the updater never adds developer
# tooling to somebody's copy of the app.
SKIP_NAMES = {".env", ".env.example", "manifest.json", ".gitignore",
              "requirements.txt", "requirements-lock.txt",
              ".DS_Store", "Thumbs.db", "Shortsmith.lnk", "Shortsmith.desktop"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".part", ".tmp"}

# assets/backgrounds holds the user's own gameplay clips. Only the README that
# explains the folder is ours to ship.
def _skip(rel: Path) -> bool:
    parts = rel.parts
    if set(parts) & SKIP_DIRS:
        return True
    if rel.name in SKIP_NAMES or rel.suffix.lower() in SKIP_SUFFIXES:
        return True
    if len(parts) >= 2 and parts[0] == "assets" and parts[1] == "backgrounds":
        return rel.name != "README.txt"
    if parts and parts[0].endswith(".app"):      # a built macOS launcher
        return True
    return False


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build() -> dict:
    files = {}
    total = 0
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if _skip(rel):
            continue
        size = p.stat().st_size
        total += size
        # Forward slashes: this is a URL path as much as a file path.
        files[rel.as_posix()] = {"sha256": sha256(p), "size": size}

    sys.path.insert(0, str(ROOT))
    from shortsmith import __version__          # noqa: E402

    return {"manifest_version": 1, "version": __version__,
            "files": files, "count": len(files), "total_bytes": total}


def main() -> int:
    data = build()
    out = ROOT / "manifest.json"
    out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    mb = data["total_bytes"] / 1024 ** 2
    print(f"manifest.json: {data['count']} files, {mb:.2f} MB, v{data['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
