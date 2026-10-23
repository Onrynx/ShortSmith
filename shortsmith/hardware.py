"""Local hardware scan — what this computer can actually run.

Used once, at first-run setup, to recommend a model and to size the generation
knobs to it. The result is cached, because nothing here changes between launches
and probing the GPU on every startup would be wasted work.

Two rules this module keeps absolutely:

1. NOTHING LEAVES THE MACHINE. Every value is read from an OS call. There is no
   network access here, no telemetry, no identifiers collected. The only thing
   the installer downloads is what the user explicitly approves.

2. IT NEVER RAISES. Hardware probing is full of vendor-specific edge cases, and
   a setup wizard that crashes because a GPU driver answered oddly is worse than
   one that says "unknown" and recommends conservatively. Every probe is
   individually guarded and falls back to None.

On VRAM specifically: do NOT use WMI's Win32_VideoController.AdapterRAM. It is a
signed 32-bit field, so it silently caps at 4 GB and misreports every card above
that — it told us a 6 GB card had 4 GB. The registry's qwMemorySize is 64-bit and
correct, and nvidia-smi is better still when it exists.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .utils import CREATE_NO_WINDOW, ROOT, STATE_DIR, log, read_json, write_json

_CACHE = STATE_DIR / "hardware.json"
_SCAN_VERSION = 1          # bump to invalidate caches after changing probes


def _run(cmd: list[str], timeout: float = 6.0) -> str:
    """Run a probe command quietly. Returns '' on any failure.

    CREATE_NO_WINDOW matters: without it every probe flashes a console window,
    which looks broken in a GUI installer.
    """
    try:
        kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             **kw)
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001 — a missing tool is a normal outcome here
        return ""


# --------------------------------------------------------------------------- #
# RAM
# --------------------------------------------------------------------------- #
def _ram() -> tuple[float, float]:
    """(total_gb, free_gb). Falls back to a conservative guess."""
    try:
        import psutil  # type: ignore
        m = psutil.virtual_memory()
        return round(m.total / 1024 ** 3, 1), round(m.available / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        pass
    try:  # Linux without psutil
        if sys.platform.startswith("linux"):
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.split()[0]) * 1024
            total = info.get("MemTotal", 0)
            free = info.get("MemAvailable", info.get("MemFree", 0))
            if total:
                return round(total / 1024 ** 3, 1), round(free / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        pass
    return 8.0, 4.0


# --------------------------------------------------------------------------- #
# GPU / VRAM
# --------------------------------------------------------------------------- #
def _gpu_nvidia() -> tuple[str | None, float | None]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits"])
    if not out:
        return None, None
    first = out.splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 2:
        return None, None
    try:
        return parts[0], round(float(parts[1]) / 1024, 1)   # MiB -> GiB
    except ValueError:
        return parts[0] or None, None


def _gpu_windows() -> tuple[str | None, float | None]:
    """Registry qwMemorySize — the 64-bit field WMI's AdapterRAM should have been."""
    name, vram = _gpu_nvidia()
    if vram:
        return name, vram
    try:
        import winreg  # type: ignore
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        best_name, best_vram = None, 0.0
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(16):
                sub = f"{i:04d}"
                try:
                    with winreg.OpenKey(root, sub) as k:
                        try:
                            size, _ = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")
                            gb = round(int(size) / 1024 ** 3, 1)
                        except FileNotFoundError:
                            gb = 0.0
                        try:
                            desc, _ = winreg.QueryValueEx(k, "DriverDesc")
                        except FileNotFoundError:
                            desc = None
                        if gb > best_vram:
                            best_vram, best_name = gb, desc
                        elif best_name is None and desc:
                            best_name = desc
                except OSError:
                    continue
        return best_name, (best_vram or None)
    except Exception:  # noqa: BLE001
        return name, None


def _gpu_macos(ram_total: float) -> tuple[str | None, float | None, bool]:
    """(name, vram_gb, unified). Apple Silicon shares one pool with the CPU."""
    apple_silicon = platform.machine() in ("arm64", "aarch64")
    name = None
    out = _run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=12)
    if out:
        try:
            data = json.loads(out)
            cards = data.get("SPDisplaysDataType") or []
            if cards:
                c = cards[0]
                name = c.get("sppci_model") or c.get("_name")
                raw = str(c.get("spdisplays_vram_shared")
                          or c.get("spdisplays_vram") or "")
                digits = "".join(ch for ch in raw if ch.isdigit())
                if digits and not apple_silicon:
                    mb = float(digits)
                    gb = mb / 1024 if mb > 64 else mb
                    return name, round(gb, 1), False
        except Exception:  # noqa: BLE001
            pass
    if apple_silicon:
        # Unified memory: the GPU can address most of system RAM, so model
        # sizing should use RAM, not a separate VRAM figure.
        return (name or "Apple Silicon GPU"), round(ram_total, 1), True
    return name, None, False


def _gpu_linux() -> tuple[str | None, float | None]:
    name, vram = _gpu_nvidia()
    if name:
        return name, vram
    out = _run(["lspci"])
    for line in out.splitlines():
        if "VGA compatible controller" in line or "3D controller" in line:
            return line.split(":")[-1].strip(), None
    return None, None


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #
def _platform_key() -> str:
    """Enough of this machine's identity to notice it is a different one."""
    return f"{platform.system()}|{platform.machine()}|{platform.node()}"


def scan(use_cache: bool = True) -> dict:
    """Describe this computer. Cached after the first run; never raises."""
    if use_cache:
        cached = read_json(_CACHE, {}) or {}
        # The cache has to match the machine reading it, not just the scanner
        # version. An install folder shared between systems — or carried on a
        # USB stick — was handing a Linux run the hardware of the Windows box
        # that happened to scan first, and every model recommendation after
        # that was for the wrong computer.
        same_machine = (cached.get("platform_key") == _platform_key())
        if cached.get("scan_version") == _SCAN_VERSION and same_machine:
            return cached

    total_ram, free_ram = _ram()
    system = platform.system()           # Windows | Darwin | Linux
    unified = False
    try:
        if system == "Windows":
            gpu_name, vram = _gpu_windows()
        elif system == "Darwin":
            gpu_name, vram, unified = _gpu_macos(total_ram)
        else:
            gpu_name, vram = _gpu_linux()
    except Exception as exc:  # noqa: BLE001
        log("warn", f"GPU probe failed ({exc}); continuing without it")
        gpu_name, vram = None, None

    try:
        free_disk = round(shutil.disk_usage(str(ROOT)).free / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        free_disk = 0.0

    info = {
        "scan_version": _SCAN_VERSION,
        "platform_key": _platform_key(),
        "os": {"Windows": "Windows", "Darwin": "macOS"}.get(system, system or "Unknown"),
        "os_release": platform.release() or "",
        "arch": platform.machine() or "",
        "apple_silicon": system == "Darwin" and platform.machine() in ("arm64", "aarch64"),
        "cpu_cores": os.cpu_count() or 0,
        "ram_total_gb": total_ram,
        "ram_free_gb": free_ram,
        "gpu_name": gpu_name,
        "vram_gb": vram,
        "unified_memory": unified,
        "disk_free_gb": free_disk,
        "python": platform.python_version(),
    }
    try:
        write_json(_CACHE, info)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not cache hardware scan: {exc}")
    return info


def rescan() -> dict:
    """Force a fresh scan (Settings -> Re-scan hardware)."""
    return scan(use_cache=False)


def usable_gb(hw: dict | None = None) -> float:
    """Memory a model can realistically occupy.

    A dedicated GPU runs the model in VRAM, so that is the limit. Apple Silicon
    shares one pool, and a CPU-only machine uses system RAM — but we never
    promise all of it, since the OS and the render pipeline need room too.
    """
    hw = hw or scan()
    vram = hw.get("vram_gb") or 0
    if vram and not hw.get("unified_memory"):
        # A model slightly larger than VRAM still runs (Ollama spills to RAM),
        # so allow some overflow rather than refusing good options outright.
        return round(max(float(vram) * 1.6, float(hw.get("ram_free_gb") or 0) * 0.7), 1)
    total = float(hw.get("ram_total_gb") or 8)
    return round(total * 0.6, 1)


def summary(hw: dict | None = None) -> str:
    """One human line for logs and the setup screen."""
    hw = hw or scan()
    bits = [f"{hw['os']} {hw.get('arch','')}".strip(),
            f"{hw['cpu_cores']} cores",
            f"{hw['ram_total_gb']} GB RAM ({hw['ram_free_gb']} free)"]
    if hw.get("gpu_name"):
        g = hw["gpu_name"]
        if hw.get("vram_gb"):
            g += f" {hw['vram_gb']} GB" + (" unified" if hw.get("unified_memory") else " VRAM")
        bits.append(g)
    bits.append(f"{hw['disk_free_gb']} GB free")
    return " · ".join(bits)


if __name__ == "__main__":       # python -m shortsmith.hardware
    info = scan(use_cache=False)
    print(summary(info))
    print(json.dumps(info, indent=2))
    print(f"\nusable for a model: ~{usable_gb(info)} GB")
