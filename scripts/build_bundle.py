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
def build_windows(out: Path, arch: str, refresh_only: bool = False) -> Path:
    """Assemble the bundle. refresh_only reuses an existing runtime.

    The runtime is the slow part — a download and a few hundred megabytes of
    pip. It does not change when the app's own code does, so re-running the
    whole thing to pick up a one-line edit is wasted time.
    """
    if refresh_only:
        if not (out / "runtime" / "python.exe").exists():
            raise SystemExit("no runtime to refresh — run without --refresh first")
        log("reusing the existing runtime")
        return _app_files_and_launchers(out)

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

    return _app_files_and_launchers(out)


def _app_files_and_launchers(out: Path) -> Path:
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
        encoding="utf-8", newline="")
    (out / "Shortsmith (show errors).bat").write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "runtime\\python.exe -m shortsmith.app\r\n"
        "echo.\r\n"
        "echo Shortsmith closed. Anything above this line is the reason.\r\n"
        "pause\r\n", encoding="utf-8", newline="")
    # Someone who just unzipped this has no idea which of these files to touch.
    # A plain .txt opens in Notepad on a double-click and needs no reader. The
    # "!" is not decoration: Explorer sorts it above every letter, so this sits
    # at the top of the folder instead of below Shortsmith.vbs where "ST" would
    # otherwise put it.
    (out / "! START HERE.txt").write_text(
        "SHORTSMITH\r\n"
        "==========\r\n"
        "\r\n"
        "Two things, then you are done.\r\n"
        "\r\n"
        "\r\n"
        "1. INSTALL OLLAMA  (once, about 2 minutes)\r\n"
        "\r\n"
        "   Go to:  https://ollama.com/download\r\n"
        "\r\n"
        "   Download it, run the installer, done. That is the engine that\r\n"
        "   writes the stories. Shortsmith starts it when you open the app\r\n"
        "   and stops it when you close it, so it is not running all day.\r\n"
        "\r\n"
        "\r\n"
        "2. OPEN SHORTSMITH\r\n"
        "\r\n"
        "   Double-click the file in this folder called:  Shortsmith\r\n"
        "\r\n"
        "   Windows may say it does not recognise the app, because this\r\n"
        "   download is not signed by a registered company. Click\r\n"
        "   More info, then Run anyway.\r\n"
        "\r\n"
        "\r\n"
        "THE FIRST TIME IT OPENS\r\n"
        "\r\n"
        "   It checks what your computer can handle and shows you a list of\r\n"
        "   writer models. Pick one and press Download. Only the one you\r\n"
        "   pick is fetched. Anything too big for your machine is greyed\r\n"
        "   out with the reason.\r\n"
        "\r\n"
        "   Then open the Backgrounds tab and drop in a few gameplay clips\r\n"
        "   (Minecraft parkour, Subway Surfers, anything). Those play behind\r\n"
        "   your videos. Without them you still get videos, just over a plain\r\n"
        "   moving background.\r\n"
        "\r\n"
        "   Then press Make.\r\n"
        "\r\n"
        "\r\n"
        "IF NOTHING HAPPENS WHEN YOU DOUBLE-CLICK\r\n"
        "\r\n"
        "   Double-click  Shortsmith (show errors)  instead. It opens the\r\n"
        "   same app but leaves the reason on screen.\r\n"
        "\r\n"
        "\r\n"
        "WHAT THIS DOES NOT DO\r\n"
        "\r\n"
        "   It does not upload anything. It does not have an account, an API\r\n"
        "   key or a subscription. It talks to Ollama on your own machine,\r\n"
        "   checks GitHub for updates, and downloads the model you choose.\r\n"
        "   That is the whole of its network use.\r\n"
        "\r\n"
        "   Posting a video to YouTube is something you do yourself.\r\n"
        "\r\n"
        "\r\n"
        "More detail is in README.md. Source: github.com/Onrynx/ShortSmith\r\n",
        encoding="utf-8", newline="")

    # The repo README opens by telling you to download Shortsmith, which is
    # absurd advice for somebody reading it from inside the download. Swap
    # that section for one that starts where they actually are.
    readme = out / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        start = text.find("## Get it")
        end = text.find("## Add gameplay clips")
        if 0 <= start < end:
            text = text[:start] + (
                "## You already have it\n\n"
                "You are reading this from inside the download, so there is\n"
                "nothing left to fetch. See **! START HERE.txt** in this folder for\n"
                "the two things to do next, or in short:\n\n"
                "1. Install Ollama from [ollama.com/download](https://ollama.com/download)\n"
                "2. Double-click **Shortsmith** in this folder\n\n"
                "The first time it opens, it checks what your computer can run and\n"
                "lets you pick a writer model. Only the one you pick is downloaded.\n\n"
            ) + text[end:]
            readme.write_text(text, encoding="utf-8")
            log("README rewritten for someone who already has the files")

    log("wrote launchers and first-run notes")
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
    ap.add_argument("--refresh", action="store_true",
                    help="reuse the existing runtime; only refresh the app files")
    args = ap.parse_args()

    system, arch = platform.system(), platform.machine()
    out = Path(args.out) if args.out else BUILD / "Shortsmith"
    out.mkdir(parents=True, exist_ok=True)
    print(f"Building Shortsmith for {system}/{arch}")

    if system == "Windows":
        build_windows(out, arch, refresh_only=args.refresh)
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
