#!/usr/bin/env python
"""Build a Shortsmith that runs without Python installed.

The old install was five steps: download, install Python, remember to tick Add
to PATH, install Ollama, run a setup script. Four of those are things a person
should never have to know about, and any one of them going wrong leaves them
stuck with an error message about an interpreter they did not ask for.

This produces a folder containing its own Python, every dependency, and the
app — so the whole install becomes: unzip, double-click. Ollama stays a
separate download, because it is a separate program that installs itself
properly and may already be there.

    python scripts/build_bundle.py            # for this machine's platform
    python scripts/build_bundle.py --zip      # and package it

What comes out keeps the source as plain readable .py files rather than a
sealed binary, so the app can still patch itself file by file and anyone can
open it and see what it does.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
PY_VERSION = "3.13.1"

# python.org's embeddable package: a complete interpreter in a zip, no
# installer, no registry, no PATH. Exactly what a bundled runtime needs.
EMBED_URL = {
    ("Windows", "AMD64"): f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip",
    ("Windows", "ARM64"): f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-arm64.zip",
}
GET_PIP = "https://bootstrap.pypa.io/get-pip.py"


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def fetch(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        log(f"cached {dest.name}")
        return dest
    log(f"downloading {url.rsplit('/', 1)[-1]} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Shortsmith-build"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as out:
        shutil.copyfileobj(r, out)
    tmp.replace(dest)
    log(f"got {dest.name} ({dest.stat().st_size / 1024 ** 2:.1f} MB)")
    return dest


def shipped_files() -> list[str]:
    """The app's own files, from the manifest, so build and update agree."""
    manifest = ROOT / "manifest.json"
    if not manifest.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_manifest.py")],
                       cwd=str(ROOT), check=True)
    return sorted(json.loads(manifest.read_text(encoding="utf-8"))["files"])


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
def build_windows(out: Path, arch: str) -> Path:
    url = EMBED_URL.get(("Windows", arch))
    if not url:
        raise SystemExit(f"no embeddable Python published for Windows/{arch}")

    cache = BUILD / "_cache"
    embed_zip = fetch(url, cache / f"python-embed-{arch}.zip")

    runtime = out / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(runtime)
    log(f"runtime unpacked ({sum(f.stat().st_size for f in runtime.rglob('*') if f.is_file()) / 1024**2:.0f} MB)")

    # The embeddable build ships with site-packages disabled; the whole point
    # here is to put packages beside it, so switch it back on.
    for pth in runtime.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8")
        if "#import site" in text:
            text = text.replace("#import site", "import site")
        # The embeddable build decides sys.path from this file alone, and it
        # only knows about itself. The app lives one level up, beside the
        # runtime folder, so say so — otherwise `-m shortsmith.app` cannot
        # find shortsmith at all.
        if ".." not in text.split():
            text = text.rstrip() + "\n..\n"
        pth.write_text(text, encoding="utf-8")
        log(f"{pth.name}: site-packages on, app folder on the path")

    get_pip = fetch(GET_PIP, cache / "get-pip.py")
    py = runtime / "python.exe"
    log("installing pip into the runtime ...")
    subprocess.run([str(py), str(get_pip), "--no-warn-script-location", "-q"],
                   check=True, cwd=str(runtime))

    lock = ROOT / "requirements-lock.txt"
    log(f"installing dependencies from {lock.name} (this is the slow part) ...")
    subprocess.run([str(py), "-m", "pip", "install", "-q",
                    "--require-hashes", "--no-warn-script-location",
                    "-r", str(lock)], check=True, cwd=str(runtime))

    # Strip what only a developer needs.
    for junk in ("pip", "pip-*.dist-info", "setuptools", "setuptools-*.dist-info",
                 "wheel", "wheel-*.dist-info", "_distutils_hack"):
        for p in (runtime / "Lib" / "site-packages").glob(junk):
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    for cache_dir in runtime.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)

    # The app itself, as plain source — minus the things only someone working
    # ON Shortsmith needs. Unzipping and finding four launchers and a pile of
    # build scripts is exactly the confusion this bundle exists to remove.
    DEV_ONLY = ("scripts/", "docs/", "requirements.txt", "requirements-lock.txt",
                ".gitignore", ".env.example")
    copied = 0
    for rel in shipped_files():
        if rel.startswith(DEV_ONLY) or rel in DEV_ONLY:
            continue
        src, dst = ROOT / rel, out / rel
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    log(f"copied {copied} app files (developer tooling left out)")

    # One double-click, no console window.
    (out / "Shortsmith.vbs").write_text(
        'Set s = CreateObject("WScript.Shell")\r\n'
        'p = CreateObject("Scripting.FileSystemObject")'
        '.GetParentFolderName(WScript.ScriptFullName)\r\n'
        's.CurrentDirectory = p\r\n'
        's.Run """" & p & "\\runtime\\pythonw.exe"" -m shortsmith.app", 0, False\r\n',
        encoding="utf-8")
    (out / "Shortsmith (show errors).bat").write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "runtime\\python.exe -m shortsmith.app\r\n"
        "echo.\r\n"
        "echo Shortsmith closed. Anything above this line is the reason.\r\n"
        "pause\r\n", encoding="utf-8")
    log("wrote launchers")
    return out


# --------------------------------------------------------------------------- #
def package(folder: Path) -> Path:
    archive = folder.with_suffix(".zip")
    if archive.exists():
        archive.unlink()
    log("compressing ...")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(folder.parent).as_posix())
    log(f"{archive.name}: {archive.stat().st_size / 1024 ** 2:.0f} MB")
    return archive


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a self-contained Shortsmith")
    ap.add_argument("--zip", action="store_true", help="also produce a .zip")
    ap.add_argument("--out", default="", help="output folder (default build/Shortsmith)")
    args = ap.parse_args()

    system, arch = platform.system(), platform.machine()
    out = Path(args.out) if args.out else BUILD / "Shortsmith"
    out.mkdir(parents=True, exist_ok=True)
    print(f"Building Shortsmith for {system}/{arch}")

    if system == "Windows":
        build_windows(out, arch)
    else:
        raise SystemExit(
            f"This builder currently produces Windows bundles only. On {system} "
            f"the same shape applies — a runtime folder beside the app — but it "
            f"needs that platform's own Python build, so run it there.")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n  {out}  ({size / 1024 ** 2:.0f} MB)")
    if args.zip:
        package(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
