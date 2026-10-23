"""Make sure ffmpeg exists, because without it nothing renders at all.

The bundle ships Python and every library, which made the install one tap —
except ffmpeg was never in it. It cuts the background clips, assembles the
video, reads durations and makes the thumbnail, so a machine without it gets
all the way through picking a model, nine gigabytes of downloads and several
minutes of writing before failing with "FFmpeg not found on PATH". The person
who most needs the app to just work is the one who hits that.

So it is fetched like the other models: once, during setup, with a progress
bar, into the app's own folder.

Into the app's own folder specifically — not onto PATH, not into Program Files.
Nothing here needs administrator rights, nothing outside this directory is
touched, and deleting the app deletes it. The resolver in utils looks here
first, so a system ffmpeg is still preferred when one exists; this is only the
fallback for machines that have none.

ffmpeg is downloaded from its publisher rather than bundled. That keeps the
app's own download at 121 MB, and means the GPL binary is distributed by the
people who build it, under their terms, rather than redistributed by us.
"""
from __future__ import annotations

import platform
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from .utils import STATE_DIR, http_download, log

TOOLS = STATE_DIR / "tools"
BIN = TOOLS / "bin"

# gyan.dev's "essentials" build: ffmpeg and ffprobe with the codecs a render
# actually uses, rather than the full build's 217 MB per binary.
GYAN_VERSION_URL = "https://www.gyan.dev/ffmpeg/builds/release-version"
GYAN_PKG = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-{v}-essentials_build.zip"
GYAN_SHA = GYAN_PKG + ".sha256"

# Static Linux builds, published alongside their own checksums.
LINUX_URL = ("https://johnvansickle.com/ffmpeg/releases/"
             "ffmpeg-release-{arch}-static.tar.xz")

MAX_BYTES = 400 * 1024 ** 2


def exe_name(stem: str) -> str:
    return stem + (".exe" if platform.system() == "Windows" else "")


def installed() -> Path | None:
    """Our own copy, if we have fetched one and it still looks right."""
    p = BIN / exe_name("ffmpeg")
    return p if p.is_file() and p.stat().st_size > 1024 else None


def have_any() -> bool:
    """Is there a usable ffmpeg anywhere — ours, or already on the machine?"""
    from .utils import has_ffmpeg
    return has_ffmpeg()


def _fetch_text(url: str, timeout: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Shortsmith"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace").strip()


# --------------------------------------------------------------------------- #
def _install_windows(on_progress=None) -> Path:
    version = _fetch_text(GYAN_VERSION_URL)
    if not version or len(version) > 20:
        raise RuntimeError("could not read the published ffmpeg version")
    url = GYAN_PKG.format(v=version)

    # The publisher's own checksum, fetched separately from the archive.
    digest = ""
    try:
        digest = _fetch_text(GYAN_SHA.format(v=version)).split()[0]
    except Exception as exc:  # noqa: BLE001
        log("warn", f"no published checksum for ffmpeg {version} ({exc})")

    TOOLS.mkdir(parents=True, exist_ok=True)
    archive = TOOLS / f"ffmpeg-{version}.zip"
    if on_progress:
        on_progress(f"Downloading ffmpeg {version}")
    http_download(url, archive, timeout=1800,
                  expect_sha256=digest or None, max_bytes=MAX_BYTES)

    if on_progress:
        on_progress("Unpacking ffmpeg")
    _extract_binaries(archive, (".exe",))
    archive.unlink(missing_ok=True)
    return BIN


def _install_posix(on_progress=None) -> Path:
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64",
            "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if not arch:
        raise RuntimeError(f"no published static ffmpeg for {machine}")
    url = LINUX_URL.format(arch=arch)

    TOOLS.mkdir(parents=True, exist_ok=True)
    archive = TOOLS / f"ffmpeg-{arch}.tar.xz"
    if on_progress:
        on_progress("Downloading ffmpeg")
    # These builds publish a .md5 rather than a .sha256; the transfer is over
    # HTTPS and the result is verified by running it, below.
    http_download(url, archive, timeout=1800, max_bytes=MAX_BYTES)

    if on_progress:
        on_progress("Unpacking ffmpeg")
    _extract_binaries(archive, ("",))
    archive.unlink(missing_ok=True)
    return BIN


def _extract_binaries(archive: Path, suffixes: tuple) -> None:
    """Pull just ffmpeg and ffprobe out, ignoring the rest of the archive."""
    BIN.mkdir(parents=True, exist_ok=True)
    wanted = {exe_name("ffmpeg"), exe_name("ffprobe")}
    found = set()

    def place(name: str, data) -> None:
        target = BIN / name
        # Never write outside BIN, whatever the archive claims its paths are.
        if target.resolve().parent != BIN.resolve():
            return
        with open(target, "wb") as out:
            shutil.copyfileobj(data, out)
        target.chmod(0o755)
        found.add(name)

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = Path(info.filename).name
                if base in wanted and not info.is_dir():
                    with z.open(info) as src:
                        place(base, src)
    else:
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                base = Path(member.name).name
                if base in wanted and member.isfile():
                    src = t.extractfile(member)
                    if src:
                        place(base, src)

    missing = wanted - found
    if missing:
        raise RuntimeError(f"the ffmpeg archive did not contain {', '.join(missing)}")


def install(on_progress=None) -> dict:
    """Fetch ffmpeg into the app's own folder. Returns what happened."""
    if installed():
        return {"ok": True, "already": True, "path": str(BIN)}
    system = platform.system()
    try:
        if system == "Windows":
            _install_windows(on_progress)
        elif system in ("Linux", "Darwin"):
            if system == "Darwin":
                # No equivalent single-file publisher for macOS that is both
                # current and checksummed, and every Mac that can run this
                # already has Homebrew or can get it in one line.
                raise RuntimeError(
                    "On macOS, install ffmpeg with:  brew install ffmpeg")
            _install_posix(on_progress)
        else:
            raise RuntimeError(f"no ffmpeg recipe for {system}")
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not install ffmpeg: {exc}")
        return {"ok": False, "error": str(exc)[:300]}

    # Prove it runs before claiming success.
    from .utils import _FFMPEG_CACHE, _is_real_ffmpeg
    binary = BIN / exe_name("ffmpeg")
    if not _is_real_ffmpeg(str(binary)):
        return {"ok": False, "error": "the downloaded ffmpeg did not run"}
    _FFMPEG_CACHE.clear()                 # pick the new one up immediately
    size = sum(f.stat().st_size for f in BIN.iterdir() if f.is_file())
    log("info", f"ffmpeg installed into {BIN} ({size / 1024 ** 2:.0f} MB)")
    return {"ok": True, "path": str(BIN), "mb": round(size / 1024 ** 2)}


__all__ = ["install", "installed", "have_any", "BIN", "TOOLS"]
