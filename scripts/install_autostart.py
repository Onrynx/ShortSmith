#!/usr/bin/env python3
"""Install Shortsmith's Discord bot as a silent background service.

ONE script for every OS — Windows, macOS and Linux. It starts hidden at login
(no console window ever) and restarts itself if it crashes, so the bot is simply
always there waiting in Discord.

    python scripts/install_autostart.py            # install + start now
    python scripts/install_autostart.py --status   # is it running?
    python scripts/install_autostart.py --uninstall

Idle cost is tiny: the bot is just an idle websocket (~50MB RAM, ~0% CPU). The
heavy AI model is loaded by Ollama only while a video is actually being made,
and unloaded again afterwards.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".shortsmith_state"
LOG = STATE / "bot.log"
LABEL = "com.shortsmith.bot"
MODULE = "shortsmith.discord_bot"

# This is a break-glass tool people run when things are broken, so it must never
# itself crash on a Windows cp1252 console when printing status emoji.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


def _py() -> str:
    """Interpreter that shows NO console window (pythonw on Windows)."""
    exe = Path(sys.executable)
    if os.name == "nt":
        pyw = exe.with_name("pythonw.exe")
        if pyw.exists():
            return str(pyw)
    return str(exe)


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# --------------------------------------------------------------------------- #
# Windows — a shortcut in the Startup folder pointing at pythonw (windowless)
# --------------------------------------------------------------------------- #
def _win_lnk() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" / "Shortsmith Bot.lnk"


def win_install() -> None:
    lnk = _win_lnk()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        f"$s.TargetPath='{_py()}';"
        f"$s.Arguments='-m {MODULE}';"
        f"$s.WorkingDirectory='{ROOT}';"
        f"$s.WindowStyle=7;"
        f"$s.Description='Shortsmith Discord bot (silent)';"
        f"$s.Save()"
    )
    r = _run(["powershell", "-NoProfile", "-Command", ps])
    if r.returncode != 0:
        raise RuntimeError(f"could not create startup shortcut: {r.stderr[:200]}")
    print(f"  installed: {lnk}")


def win_uninstall() -> None:
    lnk = _win_lnk()
    if lnk.exists():
        lnk.unlink()
    print("  removed startup shortcut")


def win_running() -> bool:
    r = _run(["powershell", "-NoProfile", "-Command",
              "@(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
              "Where-Object { $_.CommandLine -match 'discord_bot' }).Count"])
    return (r.stdout or "0").strip().split("\n")[-1].strip() not in ("0", "")


def win_start() -> None:
    subprocess.Popen([_py(), "-m", MODULE], cwd=str(ROOT),
                     stdout=open(LOG, "a", encoding="utf-8"),
                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     creationflags=0x08000000 | 0x00000200)  # NO_WINDOW|NEW_GROUP


def win_stop() -> None:
    _run(["powershell", "-NoProfile", "-Command",
          "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
          "Where-Object { $_.CommandLine -match 'discord_bot' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"])


# --------------------------------------------------------------------------- #
# macOS — a LaunchAgent (the native way; never opens a Terminal window)
# --------------------------------------------------------------------------- #
def _mac_plist() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def mac_install() -> None:
    p = _mac_plist()
    p.parent.mkdir(parents=True, exist_ok=True)
    # PATH must include Homebrew so ffmpeg/ollama are found by the agent
    p.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{_py()}</string><string>-m</string><string>{MODULE}</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{LOG}</string>
  <key>StandardErrorPath</key><string>{LOG}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
""", encoding="utf-8")
    _run(["launchctl", "unload", str(p)])
    r = _run(["launchctl", "load", "-w", str(p)])
    if r.returncode != 0:
        raise RuntimeError(f"launchctl load failed: {r.stderr[:200]}")
    print(f"  installed: {p}")


def mac_uninstall() -> None:
    p = _mac_plist()
    _run(["launchctl", "unload", "-w", str(p)])
    if p.exists():
        p.unlink()
    print("  removed LaunchAgent")


def mac_running() -> bool:
    return LABEL in (_run(["launchctl", "list"]).stdout or "")


# --------------------------------------------------------------------------- #
# Linux — a user systemd unit
# --------------------------------------------------------------------------- #
def _linux_unit() -> Path:
    return Path.home() / ".config/systemd/user/shortsmith-bot.service"


def linux_install() -> None:
    u = _linux_unit()
    u.parent.mkdir(parents=True, exist_ok=True)
    u.write_text(f"""[Unit]
Description=Shortsmith Discord bot
After=network-online.target

[Service]
ExecStart={_py()} -m {MODULE}
WorkingDirectory={ROOT}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
""", encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", "shortsmith-bot"])
    print(f"  installed: {u}")


def linux_uninstall() -> None:
    _run(["systemctl", "--user", "disable", "--now", "shortsmith-bot"])
    u = _linux_unit()
    if u.exists():
        u.unlink()
    _run(["systemctl", "--user", "daemon-reload"])
    print("  removed systemd unit")


def linux_running() -> bool:
    return (_run(["systemctl", "--user", "is-active", "shortsmith-bot"]).stdout or "").strip() == "active"


# --------------------------------------------------------------------------- #
OS = platform.system()
IMPL = {
    "Windows": (win_install, win_uninstall, win_running),
    "Darwin": (mac_install, mac_uninstall, mac_running),
    "Linux": (linux_install, linux_uninstall, linux_running),
}


def stop_only() -> None:
    """Stop the running bot but LEAVE autostart enabled — it comes back at the
    next login/reboot (this is the 'temporarily off' path)."""
    if OS == "Windows":
        win_stop()
    elif OS == "Darwin":
        _run(["launchctl", "unload", str(_mac_plist())])   # plist kept -> reloads at login
    else:
        _run(["systemctl", "--user", "stop", "shortsmith-bot"])


def restart() -> None:
    """Stop then start the bot (picks up a new token / code / clips)."""
    import time
    if OS == "Windows":
        win_stop()
        time.sleep(1)
        win_start()
    elif OS == "Darwin":
        mac_install()                       # unload + load = restart
    else:
        _run(["systemctl", "--user", "restart", "shortsmith-bot"])
    time.sleep(3)


def _check(label: str, ok: bool, hint: str = "") -> bool:
    print(f"  [{'OK' if ok else 'XX'}] {label}" + ("" if ok or not hint else f"\n         -> {hint}"))
    return ok


def doctor() -> int:
    """Diagnose exactly why the bot is offline. Checks every requirement."""
    import shutil
    import urllib.request
    print(f"\nShortsmith doctor  ({OS})\n" + "-" * 48)
    fatal_ok = True

    _check(f"Python {platform.python_version()}  [{sys.executable}]",
           sys.version_info[:2] >= (3, 9), "need Python 3.9 or newer")

    try:
        import discord  # type: ignore
        dver, dok = getattr(discord, "__version__", "?"), True
    except Exception:
        dver, dok = "", False
    fatal_ok &= _check(f"discord.py installed ({dver})", dok,
                       "pip install discord.py   (use the SAME python shown above)")

    fatal_ok &= _check("ffmpeg on PATH", shutil.which("ffmpeg") is not None,
                       "Windows: winget install Gyan.FFmpeg  ·  Mac: brew install ffmpeg")

    token = ""
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("DISCORD_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
    fatal_ok &= _check(".env has a DISCORD_BOT_TOKEN", len(token) > 20,
                       "no token -> the bot exits instantly. Discord Dev Portal -> Bot "
                       "-> Reset Token -> paste into .env after DISCORD_BOT_TOKEN=")

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        oll = True
    except Exception:
        oll = False
    _check("Ollama running (localhost:11434)", oll,
           "open the Ollama app (llama icon). Bot still comes ONLINE without it, "
           "but stories fall back to the canned one")

    bg = ROOT / "assets" / "backgrounds"
    clips = len([p for p in bg.glob("*")
                 if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".m4v")]) if bg.exists() else 0
    _check(f"gameplay clips present ({clips})", clips > 0,
           "drop parkour .mp4s into assets/backgrounds/ (else videos are plain colour)")

    _, _, running = IMPL[OS]
    is_up = running()
    _check("bot process running", is_up, "run:  python scripts/install_autostart.py --restart")

    if LOG.exists():
        tail = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-25:]
        errs = [l for l in tail if any(k in l for k in
                ("Error", "Traceback", "LoginFailure", "Improper token", "Privileged",
                 "Intents", "Exception", "ModuleNotFound"))]
        if errs:
            print("\n  Last errors in bot.log (the real reason it won't start):")
            for l in errs[-6:]:
                print("     " + l[:170])

    print("-" * 48)
    if fatal_ok and is_up:
        print("Everything needed is OK. If Discord still shows it offline, wait 30s or\n"
              "check your internet — the bot needs a connection to Discord.")
    elif fatal_ok:
        print("Requirements OK but the bot isn't running. Start it:\n"
              "   python scripts/install_autostart.py --restart")
    else:
        print("Fix the [XX] item(s) above, then:\n"
              "   python scripts/install_autostart.py --restart")
    print()
    return 0


def main() -> int:
    if OS not in IMPL:
        print(f"[!] unsupported OS: {OS}")
        return 1
    install, uninstall, running = IMPL[OS]
    STATE.mkdir(parents=True, exist_ok=True)
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").lstrip("-").lower()

    if arg == "status":
        print(f"Shortsmith bot on {OS}: {'RUNNING' if running() else 'NOT running'}")
        return 0
    if arg == "doctor":
        return doctor()
    if arg in ("restart", "start"):
        print("Restarting the Shortsmith bot ...")
        restart()
        print("bot running:", "yes" if running() else "NO — run --doctor to see why")
        return 0
    if arg == "stop":
        stop_only()
        print("Bot stopped (temporarily). It will start again at your next login,\n"
              "or bring it back now with:  --restart")
        return 0
    if arg == "uninstall":
        if OS == "Windows":
            win_stop()
        uninstall()
        print("Autostart removed. Nothing will run at login.")
        return 0

    if not (ROOT / ".env").exists():
        print("[!] No .env yet — copy .env.example to .env and add DISCORD_BOT_TOKEN first.")
        return 1

    print(f"Installing Shortsmith autostart for {OS} ...")
    install()
    if OS == "Windows":          # the shortcut only fires at next login, so start now
        if not win_running():
            win_start()
    print("""
============================================================
 Done. The bot now starts silently at login — no window, ever.
 Talk to it in Discord: `!here` once, then just chat.

   is it on?   python scripts/install_autostart.py --status
   diagnose:   python scripts/install_autostart.py --doctor
   restart:    python scripts/install_autostart.py --restart
   remove:     python scripts/install_autostart.py --uninstall

 Note: Ollama must also be running for the AI (it starts itself on
 Mac/Windows once installed). Idle cost of the bot is ~50MB RAM.
============================================================""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
