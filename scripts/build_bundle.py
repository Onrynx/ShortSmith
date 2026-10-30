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

# macOS has no equivalent of the Windows embeddable zip — python.org ships an
# installer, not a folder you can carry. python-build-standalone publishes
# relocatable builds for exactly this, which is what every tool that needs a
# portable Python on a Mac uses.
PBS_RELEASE = "20261021"
PBS_PYTHON = "3.13.17"
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/"
           f"download/{PBS_RELEASE}/cpython-{PBS_PYTHON}+{PBS_RELEASE}-"
           "{arch}-apple-darwin-install_only.tar.gz")
PBS_ARCH = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64"}
PBS_LINUX_URL = ("https://github.com/astral-sh/python-build-standalone/releases/"
                 f"download/{PBS_RELEASE}/cpython-{PBS_PYTHON}+{PBS_RELEASE}-"
                 "{arch}-unknown-linux-gnu-install_only.tar.gz")
PBS_LINUX_ARCH = {"x86_64": "x86_64", "amd64": "x86_64",
                  "aarch64": "aarch64", "arm64": "aarch64"}


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


def install_dependencies(py: Path, runtime: Path) -> None:
    """Install the locked dependencies into a runtime that has only pip.

    One package in the tree -- proxy-tools, which pywebview depends on -- has
    never been published as a wheel. PyPI carries its source and nothing else,
    so pip has to build it, and building anything needs setuptools.

    The Windows embeddable Python ships without setuptools. pip would normally
    fetch it into an isolated build environment, but --require-hashes forbids
    installing anything whose hash isn't pinned, so the isolated environment
    comes up empty and the build dies with "Cannot import setuptools.build_meta".

    This never shows up on a developer machine: pip keeps wheels it built
    before and quietly reuses them, so the source build simply doesn't happen
    again. It took a clean CI runner to surface it.

    So: install a pinned, hashed setuptools first, then build against it
    directly. Both files are hash-checked, so this adds nothing unverified.
    """
    build_lock = ROOT / "requirements-build.txt"
    log("installing pinned build tooling ...")
    subprocess.run([str(py), "-m", "pip", "install", "-q",
                    "--require-hashes", "--no-warn-script-location",
                    "-r", str(build_lock)], check=True, cwd=str(runtime))

    lock = ROOT / "requirements-lock.txt"
    log(f"installing dependencies from {lock.name} (this is the slow part) ...")
    subprocess.run([str(py), "-m", "pip", "install", "-q",
                    "--require-hashes", "--no-build-isolation",
                    "--no-warn-script-location",
                    "-r", str(lock)], check=True, cwd=str(runtime))


def strip_build_tooling(runtime: Path) -> None:
    """Remove what only the build needed. Nothing at runtime imports these."""
    roots = [runtime / "Lib" / "site-packages"]
    roots += list(runtime.glob("lib/python*/site-packages"))
    for site in roots:
        if not site.is_dir():
            continue
        # distutils-precedence.pth belongs with _distutils_hack: site.py runs
        # every .pth at startup, so removing the module but leaving the .pth
        # makes the interpreter print a ModuleNotFoundError traceback on every
        # single launch. Harmless, and it looks like the app is broken.
        for junk in ("pip", "pip-*.dist-info", "setuptools",
                     "setuptools-*.dist-info", "wheel", "wheel-*.dist-info",
                     "pkg_resources", "_distutils_hack",
                     "distutils-precedence.pth"):
            for p in site.glob(junk):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
    for cache_dir in runtime.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)


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

    install_dependencies(py, runtime)
    strip_build_tooling(runtime)

    return _app_files_and_launchers(out)


def _app_files_and_launchers(out: Path, mac: bool = False,
                             linux: bool = False) -> Path:
    # The app itself, as plain source — minus the things only someone working
    # ON Shortsmith needs. Unzipping and finding four launchers and a pile of
    # build scripts is exactly the confusion this bundle exists to remove.
    DEV_ONLY = ("scripts/", "docs/", "requirements.txt", "requirements-lock.txt",
                "requirements-build.txt", ".gitignore", ".env.example")
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

    if mac or linux:
        return _unix_launchers(out, linux=linux)

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
        "   writer models. Pick one and press Download. That one press also\r\n"
        "   fetches the video encoder, the narration voice and the caption\r\n"
        "   timer, so nothing stops halfway through your first video.\r\n"
        "   Anything too big for your machine is greyed out with the reason.\r\n"
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

    # The README is NOT rewritten here any more. Shipping a modified copy
    # meant the bundle could never match manifest.json — which describes the
    # repo's version — so every fresh install re-downloaded it on its first
    # update check for no reason. "! START HERE.txt" carries the first-run
    # guidance instead, and the bundle now matches the manifest byte for byte.

    log("wrote launchers and first-run notes")
    return out




# --------------------------------------------------------------------------- #
# macOS


def _unix_launchers(out: Path, linux: bool = False) -> Path:
    """A double-clickable launcher, and the note that goes with it.

    macOS and Linux share a runtime layout but nothing else here. A Mac
    launcher is a .command, which Finder runs on a double-click; on Linux that
    extension means nothing and the obstacle is different -- not Gatekeeper,
    but file managers that refuse to run a script until told to.

    The note was macOS-only for both platforms for a while, so Linux users
    were told their download was "not signed by a registered Apple developer"
    and that the app would check "what your Mac can handle".
    """
    ext = ".sh" if linux else ".command"
    machine = "computer" if linux else "Mac"
    launcher_name = f"Shortsmith{ext}"
    errors_name = f"Shortsmith (show errors){ext}"

    # Put back permission bits that an unzipper may have dropped. Some Linux
    # archive tools, and anything unzipped by way of Windows, lose them, and
    # the result is a launcher that does nothing at all on a double-click.
    repair = (
        "\n"
        "# An unzipper may have dropped the permission bits. Put them back\n"
        "# before anything tries to run.\n"
        'chmod +x "$0" ./runtime/bin/python3 2>/dev/null || true\n'
    )

    # macOS marks every file from a download as untrusted. A build on a real
    # Mac counted 8014 of them here -- roughly 600 are executable code, the
    # rest is everything beside it. Approving those one dialog at a time is
    # not a procedure anyone finishes, so the launcher clears the folder it
    # sits in. Approving the launcher is approving this; it is the same
    # command the instructions would otherwise ask the user to type.
    unmark = "" if linux else (
        "\n"
        "# macOS marks every file from a download as untrusted. In this folder\n"
        "# that is about eight thousand files -- its own Python, the compiled\n"
        "# libraries that do the video work, and everything beside them.\n"
        "# Clear the lot in one go.\n"
        'xattr -dr com.apple.quarantine . 2>/dev/null || true\n'
    )

    # If the interpreter still will not run, the shell leaves something like
    # "Killed: 9" on a line that closes with the window. Say it properly, and
    # fill the real path into the command so nothing has to be typed.
    guard = (
        "\n"
        "if ! ./runtime/bin/python3 -c \"\" 2>/dev/null; then\n"
        # Double quotes, not single: Mac home folders are routinely named
        # things like "John's MacBook", and an apostrophe inside single
        # quotes would end the quoting and hand the user a broken command.
        '  FIX="xattr -dr com.apple.quarantine \\"$PWD\\""\n'
        '  MSG="macOS is still blocking Shortsmith.\n'
        "\n"
        "Open Terminal, paste this line, and press enter:\n"
        "\n"
        '$FIX\n'
        "\n"
        'Then open Shortsmith again."\n'
        "  printf '%s\\n' \"$MSG\"\n"
        "  osascript -e 'on run argv\n"
        "display dialog (item 1 of argv) with title \"Shortsmith\" "
        "buttons {\"OK\"} default button 1\n"
        "end run' -- \"$MSG\" >/dev/null 2>&1\n"
        "  exit 1\n"
        "fi\n"
    ) if not linux else (
        "\n"
        "if ! ./runtime/bin/python3 -c \"\" 2>/dev/null; then\n"
        '  echo "Shortsmith cannot start: ./runtime/bin/python3 will not run."\n'
        '  echo "If you unzipped this with a tool that drops permissions, try:"\n'
        '  echo "    chmod -R +x ./runtime/bin"\n'
        "  read -n 1 -s -r -p \"Press any key to close.\"\n"
        "  exit 1\n"
        "fi\n"
    )

    launcher = out / launcher_name
    launcher.write_text(
        "#!/bin/bash\n"
        "# Shortsmith - double-click this.\n"
        'cd "$(dirname "$0")"\n'
        + repair + unmark + guard +
        "\n"
        "exec ./runtime/bin/python3 -m shortsmith.app\n",
        encoding="utf-8", newline="\n")
    launcher.chmod(0o755)

    errors = out / errors_name
    errors.write_text(
        "#!/bin/bash\n"
        'cd "$(dirname "$0")"\n'
        + repair + unmark + guard +
        "\n"
        "./runtime/bin/python3 -m shortsmith.app\n"
        "echo\n"
        'echo "Shortsmith closed. Anything above this line is the reason."\n'
        'read -n 1 -s -r -p "Press any key to close."\n',
        encoding="utf-8", newline="\n")
    errors.chmod(0o755)

    if linux:
        step_two = (
            "2. LET YOUR SYSTEM RUN IT  (once)\n"
            "\n"
            "   The launcher is already marked executable inside the download,\n"
            "   but most Linux file managers will not run a script on a plain\n"
            "   double-click until you allow it.\n"
            "\n"
            f"   Double-click '{launcher_name}'. If a box appears, choose Run,\n"
            "   or Run in Terminal.\n"
            "\n"
            "   If it opens in a text editor instead, right-click it, open\n"
            "   Properties, and tick the option that allows running it as a\n"
            "   program. Then double-click again.\n"
            "\n"
            "   If you would rather use a terminal, open one in this folder\n"
            "   and type:\n"
            "\n"
            f"       ./{launcher_name}\n"
        )
    else:
        # Order matters more than wording here. The note used to say
        # "double-click it, macOS will refuse, then go and fix it" -- which
        # walks every Mac user into a dialog whose DEFAULT, highlighted button
        # is "Move to Trash". Press Return out of habit and Shortsmith is
        # deleted. A run on macOS 26 photographed exactly that.
        #
        # The dialog only appears because the files are marked as downloaded.
        # Clearing the mark first means it never appears at all. Same command,
        # same effort, no dialog offering to bin the app.
        step_two = (
            "2. UNLOCK IT  (once, about twenty seconds)\n"
            "\n"
            "   SKIP THIS if you installed with the one-line command from\n"
            "   onrynx.github.io/ShortSmith -- nothing was ever locked, and\n"
            "   Shortsmith opens on a double-click.\n"
            "\n"
            "   OTHERWISE DO IT BEFORE YOU OPEN SHORTSMITH THE FIRST TIME.\n"
            "\n"
            "   Shortsmith is not signed by a registered Apple developer --\n"
            "   that costs 99 dollars a year -- so macOS marks anything a\n"
            "   browser downloads as untrusted. The tag comes from the\n"
            "   browser, not from the file: the same download fetched by the\n"
            "   one-line command carries no tag and is never blocked.\n"
            "\n"
            "   Open Terminal: press Command and Space together, type\n"
            "   Terminal, press enter.\n"
            "\n"
            "   Type this, including the space at the end:\n"
            "\n"
            "       xattr -dr com.apple.quarantine \n"
            "\n"
            "   Now drag this Shortsmith folder onto the Terminal window --\n"
            "   that fills in its location, so there is nothing to type --\n"
            "   and press enter. Nothing is printed when it works. Close\n"
            "   Terminal.\n"
            "\n"
            f"   Now double-click '{launcher_name}' and it opens normally.\n"
            "\n"
            "\n"
            "   IF YOU ALREADY DOUBLE-CLICKED IT\n"
            "\n"
            "   You will have seen a box saying 'Not Opened', offering\n"
            "   'Move to Trash' and 'Done'.\n"
            "\n"
            "   CLICK 'Done'. Never 'Move to Trash' -- that deletes\n"
            "   Shortsmith, and it is the highlighted button, so do not just\n"
            "   press Return.\n"
            "\n"
            "   Then do the Terminal step above and open it again.\n"
            "\n"
            "   There may also be an 'Open Anyway' button under\n"
            "   Apple menu > System Settings > Privacy & Security, in the\n"
            "   Security section near the bottom, for about an hour after the\n"
            "   refusal. If you see it, clicking it works too.\n"
        )

    (out / "! START HERE.txt").write_text(
        "SHORTSMITH\n"
        "==========\n"
        "\n"
        "Three things, then you are done.\n"
        "\n"
        "\n"
        "1. INSTALL OLLAMA  (once, about 2 minutes)\n"
        "\n"
        "   Go to:  https://ollama.com/download\n"
        "\n"
        "   That is the engine that writes the stories. Shortsmith starts it\n"
        "   when you open the app and stops it when you close it.\n"
        "\n"
        "\n"
        + step_two +
        "\n"
        "\n"
        "3. OPEN SHORTSMITH\n"
        "\n"
        f"   Double-click:  {launcher_name}\n"
        "\n"
        "\n"
        "THE FIRST TIME IT OPENS\n"
        "\n"
        f"   It checks what your {machine} can handle and shows you a list of\n"
        "   writer models. Pick one and press Download. That one press also\n"
        "   fetches the video encoder, the narration voice and the caption\n"
        "   timer.\n"
        "\n"
        "   Then open the Backgrounds tab and drop in a few gameplay clips.\n"
        "   Those play behind your videos.\n"
        "\n"
        "   Then press Make.\n"
        "\n"
        "\n"
        "IF NOTHING HAPPENS\n"
        "\n"
        f"   Open  '{errors_name}'  instead. It leaves the reason on screen.\n"
        "\n"
        "\n"
        "WHAT THIS DOES NOT DO\n"
        "\n"
        "   It does not upload anything, has no account and no API key. It\n"
        f"   talks to Ollama on your own {machine}, checks GitHub for updates,\n"
        "   and downloads the models you choose. That is all.\n"
        "\n"
        "\n"
        "More detail is in README.md. Source: github.com/Onrynx/ShortSmith\n",
        encoding="utf-8", newline="\n")

    log("wrote launchers and first-run notes")
    return out


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #
def build_linux(out: Path, arch: str, refresh_only: bool = False) -> Path:
    """Same shape as the others: a relocatable Python beside the app."""
    import tarfile

    if refresh_only:
        if not (out / "runtime" / "bin" / "python3").exists():
            raise SystemExit("no runtime to refresh — run without --refresh first")
        log("reusing the existing runtime")
        return _app_files_and_launchers(out, linux=True)

    key = PBS_LINUX_ARCH.get(arch.lower())
    if not key:
        raise SystemExit(f"no relocatable Python published for Linux/{arch}")

    tarball = fetch(PBS_LINUX_URL.format(arch=key),
                    BUILD / "_cache" / f"python-linux-{key}.tar.gz")
    runtime = out / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as t:
        t.extractall(out)
    (out / "python").rename(runtime)
    log(f"runtime unpacked ({sum(f.stat().st_size for f in runtime.rglob('*') if f.is_file()) / 1024**2:.0f} MB)")

    py = runtime / "bin" / "python3"
    py.chmod(0o755)
    install_dependencies(py, runtime)
    strip_build_tooling(runtime)
    return _app_files_and_launchers(out, linux=True)


# --------------------------------------------------------------------------- #
def build_macos(out: Path, arch: str, refresh_only: bool = False) -> Path:
    """Assemble a Mac bundle. Must be run ON a Mac.

    Not because of stubbornness — pip resolves wheels for the machine it runs
    on, and numpy, Pillow, onnxruntime and ctranslate2 are all compiled. Asking
    Windows to assemble a Mac's binaries produces something nobody has ever
    executed, and the first person to find out would be a stranger.
    """
    import tarfile

    if refresh_only:
        if not (out / "runtime" / "bin" / "python3").exists():
            raise SystemExit("no runtime to refresh — run without --refresh first")
        log("reusing the existing runtime")
        return _app_files_and_launchers(out, mac=True)

    key = PBS_ARCH.get(arch)
    if not key:
        raise SystemExit(f"no relocatable Python published for macOS/{arch}")

    cache = BUILD / "_cache"
    tarball = fetch(PBS_URL.format(arch=key), cache / f"python-macos-{key}.tar.gz")

    runtime = out / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as t:
        # The archive contains a top-level "python/" directory.
        t.extractall(out)
    (out / "python").rename(runtime)
    log(f"runtime unpacked ({sum(f.stat().st_size for f in runtime.rglob('*') if f.is_file()) / 1024**2:.0f} MB)")

    py = runtime / "bin" / "python3"
    py.chmod(0o755)
    install_dependencies(py, runtime)
    strip_build_tooling(runtime)

    return _app_files_and_launchers(out, mac=True)



# --------------------------------------------------------------------------- #
ASSET_NAME = {"Windows": "Shortsmith-Windows.zip",
              "Darwin": "Shortsmith-macOS.zip",
              "Linux": "Shortsmith-Linux.zip"}


def asset_name(system: str | None = None) -> str:
    """What this platform's download must be called.

    The download page asks GitHub which assets exist and matches these exact
    names, so a build that names its zip anything else never appears on it.
    """
    return ASSET_NAME.get(system or platform.system(), "Shortsmith.zip")


def package(folder: Path) -> Path:
    """Zip the bundle, keeping the parts a Unix machine needs to run it.

    A plain zipfile.write drops the executable bit and follows symlinks. On
    Windows neither matters; on a Mac both are fatal — the launcher would not
    be runnable, and the Python framework's symlinks would be expanded into
    several duplicate copies of the whole runtime.
    """
    archive = folder.parent / asset_name()
    if archive.exists():
        archive.unlink()
    log("compressing ...")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(folder.rglob("*")):
            rel = p.relative_to(folder.parent).as_posix()
            if p.is_symlink():
                info = zipfile.ZipInfo(rel)
                info.create_system = 3
                info.external_attr = (0xA000 | 0o777) << 16
                z.writestr(info, os.readlink(p))
                continue
            if not p.is_file():
                continue
            info = zipfile.ZipInfo.from_file(p, rel)
            info.compress_type = zipfile.ZIP_DEFLATED
            if os.name != "nt":
                info.create_system = 3                      # Unix
                info.external_attr = (p.stat().st_mode & 0xFFFF) << 16
            with open(p, "rb") as src, z.open(info, "w") as dst:
                shutil.copyfileobj(src, dst)
    log(f"{archive.name}: {archive.stat().st_size / 1024 ** 2:.0f} MB")
    return archive


def check_download_page() -> None:
    """The page offers a download per platform by matching the asset's name.

    It asks GitHub which assets a release carries and shows a button for each
    one it recognises, so a name only it knows about is simply never offered.
    That is silent: the build succeeds, the asset uploads, and the button
    never appears. It happened to Linux, which the page looked for as
    Shortsmith-Linux.tar.gz while the builder had always produced a .zip.
    """
    page = ROOT / "docs" / "index.html"
    if not page.exists():
        return
    text = page.read_text(encoding="utf-8")
    missing = [n for n in ASSET_NAME.values() if n not in text]
    if missing:
        raise SystemExit(
            "docs/index.html does not know these asset names, so it will "
            "never offer them:\n    " + "\n    ".join(missing) +
            "\n\n  Update the BUILDS table in docs/index.html to match "
            "ASSET_NAME in this script.")
    log("download page knows all three asset names")


def verify_against_manifest(out: Path) -> None:
    """Every file the manifest lists must be in the bundle, byte for byte.

    An installed copy updates by hashing what it has and fetching whatever
    differs from the manifest. So a file that is listed but not shipped is a
    difference that can never be resolved: every fresh install downloads it on
    its first update check, finds it still missing next time, and downloads it
    again for ever.

    That has happened twice. Once when the bundle rewrote its own README, and
    once when requirements-build.txt was added to the repo but excluded from
    the bundle without being excluded from the manifest. Both were invisible
    until someone looked. Checking here makes the build fail instead.
    """
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    listed = manifest["files"]
    missing, differing = [], []
    for rel, meta in (listed.items() if isinstance(listed, dict)
                      else [(f["path"], f) for f in listed]):
        want = meta["sha256"] if isinstance(meta, dict) else meta
        p = out / rel
        if not p.exists():
            missing.append(rel)
        elif hashlib.sha256(p.read_bytes()).hexdigest() != want:
            differing.append(rel)

    if missing or differing:
        lines = ["the bundle does not match manifest.json:"]
        for rel in missing:
            lines.append(f"    listed but not shipped: {rel}")
        for rel in differing:
            lines.append(f"    shipped with different contents: {rel}")
        lines.append("")
        lines.append("  Every fresh install would download these on its first")
        lines.append("  update check and never stop. Either ship the file, or")
        lines.append("  add it to SKIP_NAMES in scripts/make_manifest.py.")
        raise SystemExit("\n  ".join(lines))

    total = len(listed)
    log(f"bundle matches manifest ({total}/{total} files)")


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
    elif system == "Darwin":
        build_macos(out, arch, refresh_only=args.refresh)
    elif system == "Linux":
        build_linux(out, arch, refresh_only=args.refresh)
    else:
        raise SystemExit(f"No bundle recipe for {system}.")

    check_download_page()
    verify_against_manifest(out)

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n  {out}  ({size / 1024 ** 2:.0f} MB)")
    if args.zip:
        package(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
