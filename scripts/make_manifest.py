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
def check_line_endings(paths: list[Path], root: Path) -> None:
    """Refuse to write a manifest describing bytes no bundle will contain.

    The manifest is hashed from the working copy; a bundle built anywhere else
    is made from a fresh checkout. If a file on disk differs from what git
    stores by nothing but line endings, those two can never agree, and every
    installed copy re-downloads that file on every update check for ever.

    This is not hypothetical and it is not loud. assets/backgrounds/README.txt
    sat with CRLF on disk and LF in the repository; git status called the tree
    clean, because its cached stat info meant it never re-read the file. The
    published manifest and the published bundles disagreed about one file and
    nothing anywhere said so.

    .gitattributes keeps the two in step from here on. This catches anything
    that slipped through before it ships.
    """
    import subprocess

    bad = []
    for p in paths:
        rel = p.relative_to(root).as_posix()
        blob = subprocess.run(["git", "cat-file", "-p", f"HEAD:{rel}"],
                              cwd=root, capture_output=True)
        if blob.returncode != 0:          # new file, not committed yet
            continue
        disk = p.read_bytes()
        if disk == blob.stdout:
            continue
        if disk.replace(b"\r\n", b"\n") == blob.stdout:
            bad.append((rel, len(disk), len(blob.stdout)))

    if bad:
        lines = ["line endings on disk differ from the repository:"]
        for rel, d, b in bad:
            lines.append(f"    {rel}: {d} bytes on disk, {b} in git")
        lines.append("")
        lines.append("  A bundle built from a checkout can never match this")
        lines.append("  manifest. Restore the working copy first:")
        lines.append("    git cat-file -p HEAD:<path> > <path>")
        raise SystemExit("\n  ".join(lines))


# Anything the bundle deliberately leaves out must be skipped here too. The
# manifest is what an installed copy compares itself against, so a file listed
# here but absent from the bundle is a difference that can never be resolved:
# every fresh install would download it on its first update check, for ever.
SKIP_NAMES = {".env", ".env.example", "manifest.json", ".gitignore",
              "requirements.txt", "requirements-lock.txt",
              "requirements-build.txt", "requirements-build.in",
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
    shipped = [p for p in sorted(ROOT.rglob("*"))
               if p.is_file() and not _skip(p.relative_to(ROOT))]
    check_line_endings(shipped, ROOT)
    for p in shipped:
        rel = p.relative_to(ROOT)
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
