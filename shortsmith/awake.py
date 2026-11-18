"""Keep the computer awake while a video is being made.

A render takes minutes, and a laptop left alone for minutes goes to sleep.
Nothing stopped it: two renders on one machine both froze at 18:49 and both
carried on at 20:41, which is what a closed lid looks like from the inside.
To the person waiting it looks like a video that took two hours.

Each system has its own way of saying "something is working, do not sleep":

  Windows   SetThreadExecutionState, held by the calling thread
  macOS     caffeinate, tied to this process so it cannot outlive it
  Linux     systemd-inhibit, where it exists

The screen is still allowed to turn off; only the sleep that stops the work is
held back. Nothing here raises: a machine that cannot be kept awake still
renders, it just may stall the way it always could.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .utils import log

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

_helper: subprocess.Popen | None = None
_held = False


def hold() -> bool:
    """Ask the system not to sleep. Safe to call twice."""
    global _helper, _held
    if _held:
        return True
    try:
        if os.name == "nt":
            import ctypes
            ok = ctypes.windll.kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
            _held = bool(ok)
        elif sys.platform == "darwin":
            # -i: no idle sleep.  -w: exit when this process does, so a crash
            # can never leave the Mac awake for good.
            _helper = subprocess.Popen(
                ["caffeinate", "-i", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            _held = True
        elif shutil.which("systemd-inhibit"):
            _helper = subprocess.Popen(
                ["systemd-inhibit", "--what=idle:sleep", "--who=Shortsmith",
                 "--why=Making a video", "--mode=block", "sleep", "infinity"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            _held = True
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not keep the computer awake: {exc}")
        _held = False
    return _held


def release() -> None:
    """Let the system sleep again."""
    global _helper, _held
    try:
        if os.name == "nt" and _held:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        if _helper is not None:
            _helper.terminate()
    except Exception:  # noqa: BLE001
        pass
    _helper = None
    _held = False
