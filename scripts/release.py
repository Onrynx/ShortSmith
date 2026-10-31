#!/usr/bin/env python
"""Cut a release in one command.

Publishing Shortsmith by hand is six steps in a fixed order, and getting the
order wrong fails quietly. The manifest in particular has to be regenerated
AFTER the last code change and BEFORE the push, or every installed copy keeps
checking against hashes that no longer describe the files — and the update
fails closed with a checksum error nobody can interpret.

So the order lives here instead of in somebody's memory:

    1. refuse to start on a dirty tree or the wrong branch
    2. set the version in shortsmith/__init__.py
    3. check CHANGELOG.md actually mentions it
    4. regenerate manifest.json from the final files
    5. commit and push        -> every INSTALLED copy can now update itself
    6. build and zip the bundle
    7. publish a GitHub release with that zip attached
                              -> every NEW downloader starts here

Steps 5 and 7 serve different people. An existing install never needs the zip;
it patches itself from the manifest. A fresh download never sees the manifest
until it has run once. The release exists so that first run starts from
something recent rather than from whatever was current months ago.

    python scripts/release.py --version 2.4.0
    python scripts/release.py --version 2.4.0 --dry-run      # say, do nothing
    python scripts/release.py --version 2.4.0 --no-bundle    # code only

Uploading needs a GitHub token with "repo" scope, read from the environment so
it is never written to a file:

    $env:GITHUB_TOKEN = "ghp_..."        (PowerShell, this session only)
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "Onrynx/ShortSmith"
ASSET = "Shortsmith-Windows.zip"
BRANCH_REMOTE = "main"          # the branch on GitHub the manifest is read from

VERSION_RE = re.compile(r'(__version__\s*=\s*")([^"]+)(")')


class Stop(SystemExit):
    pass


def say(step: str, msg: str) -> None:
    print(f"  [{step}] {msg}", flush=True)


def git(*args, check=True, timeout=120) -> str:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise Stop(f"git {' '.join(args)} failed:\n{(r.stderr or r.stdout).strip()}")
    return (r.stdout or "").strip()


def run_py(script: str, *args) -> None:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                       cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        raise Stop(f"{script} failed:\n{(r.stderr or r.stdout).strip()}")
    for line in (r.stdout or "").strip().splitlines():
        say("..", line.strip())


# --------------------------------------------------------------------------- #
def check_clean(allow_dirty: bool) -> None:
    dirty = git("status", "--porcelain")
    if dirty and not allow_dirty:
        raise Stop("The working tree has uncommitted changes. Commit them "
                   "first, or pass --allow-dirty if they are meant to go into "
                   "this release:\n" + dirty)
    upstream = git("rev-parse", "--abbrev-ref", "@{u}", check=False)
    if not upstream:
        raise Stop("This branch is not tracking a remote. Set one with "
                   "git branch --set-upstream-to=<remote>/main")
    say("1", f"tree is usable, tracking {upstream}")


def set_version(version: str) -> str:
    init = ROOT / "shortsmith" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        raise Stop("could not find __version__ in shortsmith/__init__.py")
    was = m.group(2)
    if was != version:
        init.write_text(VERSION_RE.sub(rf"\g<1>{version}\g<3>", text, count=1),
                        encoding="utf-8")
        say("2", f"version {was} -> {version}")
    else:
        say("2", f"version already {version}")
    return was


def check_changelog(version: str) -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^##\s+v?{re.escape(version)}\s*$", text, re.M):
        raise Stop(f"CHANGELOG.md has no '## {version}' section. The app reads "
                   f"the published changelog to decide whether an update "
                   f"exists, so a release without one tells every install that "
                   f"nothing changed.")
    say("3", f"CHANGELOG.md has a {version} section")


def zip_bundle() -> Path:
    folder = ROOT / "build" / "Shortsmith"
    if not folder.exists():
        raise Stop("no build/Shortsmith — run scripts/build_bundle.py first")
    archive = ROOT / "build" / ASSET
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(folder.parent).as_posix())
    say("6", f"{ASSET}: {archive.stat().st_size / 1024 ** 2:.0f} MB")
    return archive


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
def api(path: str, token: str, method="GET", payload=None, timeout=60):
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Shortsmith-release",
        **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if body else {}


def publish(version: str, archive: Path, token: str, notes: str) -> str:
    tag = f"v{version}"
    try:
        rel = api(f"/repos/{REPO}/releases/tags/{tag}", token)
        say("7", f"release {tag} already exists; replacing its asset")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        rel = api(f"/repos/{REPO}/releases", token, "POST", {
            "tag_name": tag, "target_commitish": BRANCH_REMOTE,
            "name": f"Shortsmith {version}", "body": notes,
            "draft": False, "prerelease": False, "make_latest": "true"})
        say("7", f"created release {tag}")

    for a in rel.get("assets", []):
        if a["name"] == ASSET:
            api(f"/repos/{REPO}/releases/assets/{a['id']}", token, "DELETE")
            say("7", "removed the previous asset")

    upload = rel["upload_url"].split("{")[0] + f"?name={ASSET}"
    ctype = mimetypes.guess_type(ASSET)[0] or "application/octet-stream"
    body = archive.read_bytes()
    req = urllib.request.Request(upload, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": ctype,
        "Content-Length": str(len(body)),
        "User-Agent": "Shortsmith-release"})
    say("7", f"uploading {len(body) / 1024 ** 2:.0f} MB ...")
    with urllib.request.urlopen(req, timeout=1800) as r:
        r.read()
    return f"https://github.com/{REPO}/releases/tag/{tag}"


def changelog_section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^##\s+v?{re.escape(version)}\s*$", text, re.M)
    if not m:
        return ""
    nxt = re.search(r"^##\s+", text[m.end():], re.M)
    body = text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]
    return body.strip()[:4000]


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Cut a Shortsmith release")
    ap.add_argument("--version", required=True, help="e.g. 2.4.0")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and change nothing")
    ap.add_argument("--no-bundle", action="store_true",
                    help="push code only; do not build or upload a zip")
    ap.add_argument("--full-build", action="store_true",
                    help="rebuild the runtime too (slow; needed when a "
                         "dependency or the Python version changed)")
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()

    version = args.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise Stop("version must look like 2.4.0")

    print(f"\nReleasing Shortsmith {version}\n")

    if args.dry_run:
        print("  DRY RUN: nothing will be changed\n")
        for n, line in enumerate([
                "refuse to start on a dirty tree",
                f"check CHANGELOG.md has a '## {version}' section",
                f"set __version__ to {version}",
                "regenerate manifest.json  <- installed copies update from this",
                "commit and push           <- installed copies can now update",
                "build and zip the bundle" + (" (skipped)" if args.no_bundle else ""),
                "publish the release       <- new downloads start from this",
        ], 1):
            print(f"  [{n}] {line}")
        print()
        return 0

    # Every check first, then the first write. The earlier order set the
    # version in __init__.py and only then noticed the changelog was missing,
    # which left the file modified by a release that never happened.
    check_clean(args.allow_dirty)
    check_changelog(version)
    set_version(version)

    say("4", "regenerating manifest.json")
    run_py("make_manifest.py")

    if git("status", "--porcelain"):
        git("add", "-A")
        git("commit", "-m", f"Release {version}")
        say("5", f"committed {git('rev-parse', '--short', 'HEAD')}")
    git("push")
    say("5", "pushed — installed copies can update from here in ~5 minutes")

    if args.no_bundle:
        print(f"\n  Done (code only). Existing installs will pick this up.\n")
        return 0

    say("6", "building the bundle")
    run_py("build_bundle.py", *([] if args.full_build else ["--refresh"]))
    archive = zip_bundle()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print(f"\n  Built: {archive}")
        print("  No GITHUB_TOKEN set, so the release was not published.")
        print("  Either set one and re-run, or upload that file by hand at:")
        print(f"    https://github.com/{REPO}/releases/new\n")
        return 0

    url = publish(version, archive, token, changelog_section(version))
    print(f"\n  Published: {url}")
    print(f"  Download:  https://github.com/{REPO}/releases/latest/download/{ASSET}\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Stop as exc:
        print(f"\n  STOPPED: {exc}\n")
        raise SystemExit(1)
