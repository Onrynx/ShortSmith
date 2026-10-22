"""What kind of connection is this, and should a big download wait?

A writer model is two to twenty gigabytes. Starting that on a phone hotspot
without saying so is the kind of thing that costs somebody real money, and the
app is the only one in a position to notice — the person clicking Download is
looking at a model name, not at their connection.

So: ask the operating system what the connection costs, and only interrupt when
the answer is "this one is metered".

Every platform already tracks this, because every platform needs it for its own
update scheduling:

    Windows   WinRT NetworkInformation — the same cost flag Windows Update uses
    macOS     the active interface, and whether it is a phone tether
    Linux     NetworkManager's per-connection METERED field

Nothing here contacts the network. It asks the OS about the link, never about
what travels over it, and never about who or where you are. When the answer is
not available the result is "unknown", which does not warn — a false alarm on
every download would train people to click through the one that mattered.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import time

from .utils import log, no_window_kwargs

# Asking costs a subprocess, and the screen may ask a few times in a row.
_CACHE: dict = {}
_CACHE_TTL = 15.0

UNKNOWN = {"kind": "unknown", "metered": None, "roaming": False,
           "near_limit": False, "label": "your connection", "warn": False,
           "reason": "", "checked": False}


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, **no_window_kwargs())
        return (out.stdout or "") + (out.stderr or "")
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
_PS_COST = r"""
try {
  [void][Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]
  $p = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
  if ($null -eq $p) { Write-Output 'OFFLINE'; exit }
  $c = $p.GetConnectionCost()
  Write-Output ("COST=" + $c.NetworkCostType)
  Write-Output ("ROAM=" + $c.Roaming)
  Write-Output ("LIMIT=" + $c.OverDataLimit)
  Write-Output ("NEAR=" + $c.ApproachingDataLimit)
  Write-Output ("WLAN=" + $p.IsWlanConnectionProfile)
  Write-Output ("WWAN=" + $p.IsWwanConnectionProfile)
} catch { Write-Output 'UNKNOWN' }
"""


def _windows() -> dict:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return dict(UNKNOWN)
    text = _run([exe, "-NoProfile", "-NonInteractive", "-Command", _PS_COST], timeout=12)
    if "OFFLINE" in text:
        return {**UNKNOWN, "kind": "offline", "label": "no connection", "checked": True}
    if "COST=" not in text:
        return dict(UNKNOWN)

    def field(name: str) -> str:
        for line in text.splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip()
        return ""

    cost = field("COST").lower()
    wwan = field("WWAN").lower() == "true"
    wlan = field("WLAN").lower() == "true"
    # Unrestricted is a normal home connection. Fixed means a capped plan and
    # Variable means paid by the byte — Windows sets both for mobile data and
    # for anything the user has ticked "metered", including a phone hotspot.
    metered = cost in ("fixed", "variable")
    kind = "mobile" if wwan else ("wifi" if wlan else "ethernet")
    return {
        "kind": kind,
        "metered": metered,
        "roaming": field("ROAM").lower() == "true",
        "near_limit": field("NEAR").lower() == "true" or field("LIMIT").lower() == "true",
        "checked": True,
    }


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #
def _macos() -> dict:
    # scutil names the interface carrying the default route.
    nwi = _run(["scutil", "--nwi"])
    if "Network interfaces:" in nwi and "utun" not in nwi and not nwi.strip():
        return {**UNKNOWN, "kind": "offline", "label": "no connection", "checked": True}
    iface = ""
    for line in nwi.splitlines():
        s = line.strip()
        if s.startswith("Network interfaces:"):
            parts = s.split(":", 1)[1].split()
            if parts:
                iface = parts[0]
            break
    if not iface:
        return dict(UNKNOWN)

    hardware = _run(["networksetup", "-listallhardwareports"])
    kind, label = "ethernet", "a wired connection"
    block = ""
    for chunk in hardware.split("Hardware Port:"):
        if f"Device: {iface}" in chunk:
            block = chunk.lower()
            break
    if "wi-fi" in block or "airport" in block:
        kind, label = "wifi", "Wi-Fi"
    elif "iphone" in block or "ipad" in block or "bluetooth pan" in block:
        # A tethered phone: the one case macOS does not flag but the user pays for.
        kind, label = "mobile", "your phone's connection"

    return {"kind": kind, "metered": kind == "mobile", "roaming": False,
            "near_limit": False, "label": label, "checked": True}


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #
def _linux() -> dict:
    if not shutil.which("nmcli"):
        return dict(UNKNOWN)
    # NetworkManager tracks metered per connection and exposes it directly.
    text = _run(["nmcli", "-t", "-f", "TYPE,STATE,METERED", "connection", "show", "--active"])
    if not text.strip():
        return {**UNKNOWN, "kind": "offline", "label": "no connection", "checked": True}
    kind, metered = "ethernet", False
    for line in text.splitlines():
        bits = line.split(":")
        if len(bits) < 3 or bits[1] != "activated":
            continue
        typ, cost = bits[0].lower(), bits[2].lower()
        if "wireless" in typ or "wifi" in typ:
            kind = "wifi"
        elif "gsm" in typ or "cdma" in typ or "wwan" in typ:
            kind = "mobile"
        # nmcli says "yes"/"no", or "yes (guessed)".
        if cost.startswith("yes"):
            metered = True
        break
    return {"kind": kind, "metered": metered or kind == "mobile",
            "roaming": False, "near_limit": False, "checked": True}


# --------------------------------------------------------------------------- #
# What the app asks for
# --------------------------------------------------------------------------- #
_LABELS = {"wifi": "Wi-Fi", "ethernet": "a wired connection",
           "mobile": "mobile data", "offline": "no connection",
           "unknown": "your connection"}


def status(use_cache: bool = True) -> dict:
    """The connection, and whether a large download should ask first."""
    now = time.time()
    if use_cache and _CACHE.get("at", 0) + _CACHE_TTL > now:
        return dict(_CACHE["value"])

    system = platform.system()
    try:
        if system == "Windows":
            info = _windows()
        elif system == "Darwin":
            info = _macos()
        elif system == "Linux":
            info = _linux()
        else:
            info = dict(UNKNOWN)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not read the connection type: {exc}")
        info = dict(UNKNOWN)

    info.setdefault("label", _LABELS.get(info.get("kind", "unknown"), "your connection"))
    if info.get("label") in (None, "", "your connection"):
        info["label"] = _LABELS.get(info.get("kind", "unknown"), "your connection")

    # Only three things are worth stopping someone for, and "we could not tell"
    # is not one of them.
    info["warn"] = bool(info.get("metered") or info.get("roaming")
                        or info.get("near_limit"))
    # The reason has to name what is actually costing money. A phone hotspot
    # IS Wi-Fi, so "you are on Wi-Fi, carry on?" reads as a non-sequitur —
    # what matters is that the link is metered, however it is carried.
    if info.get("roaming"):
        info["reason"] = "you are roaming"
    elif info.get("near_limit"):
        info["reason"] = "you are close to your data limit"
    elif info.get("metered"):
        info["reason"] = ("you are on mobile data" if info.get("kind") == "mobile"
                          else f"this {info['label']} connection is set as metered")
    else:
        info["reason"] = ""

    _CACHE["at"], _CACHE["value"] = now, dict(info)
    return dict(info)


def describe(download_gb: float) -> dict:
    """status(), plus the sentence the screen should show for this download."""
    info = status()
    size = f"{download_gb:.1f} GB" if download_gb else "this model"
    if info.get("kind") == "offline":
        info["message"] = ("You do not appear to be online, so this download "
                           "will not get far.")
    elif info["warn"]:
        # Built from the reason, not the link type: the reason is the thing
        # that will show up on somebody's bill.
        info["message"] = (f"This download is {size} and {info['reason']}. "
                           f"Carry on?")
    else:
        info["message"] = ""
    info["download_gb"] = download_gb
    return info


__all__ = ["status", "describe"]
