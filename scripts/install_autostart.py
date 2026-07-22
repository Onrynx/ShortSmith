#!/usr/bin/env python3
"""Install StoryForge's Discord bot as a silent background service.

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
STATE = ROOT / ".storyforge_state"
LOG = STATE / "bot.log"
LABEL = "com.storyforge.bot"
MODULE = "storyforge.discord_bot"


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
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" / "StoryForge Bot.lnk"


def win_install() -> None:
    lnk = _win_lnk()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        f"$s.TargetPath='{_py()}';"
        f"$s.Arguments='-m {MODULE}';"
        f"$s.WorkingDirectory='{ROOT}';"
        f"$s.WindowStyle=7;"
        f"$s.Description='StoryForge Discord bot (silent)';"
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
    return Path.home() / ".config/systemd/user/storyforge-bot.service"


def linux_install() -> None:
    u = _linux_unit()
    u.parent.mkdir(parents=True, exist_ok=True)
    u.write_text(f"""[Unit]
Description=StoryForge Discord bot
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
    _run(["systemctl", "--user", "enable", "--now", "storyforge-bot"])
    print(f"  installed: {u}")


def linux_uninstall() -> None:
    _run(["systemctl", "--user", "disable", "--now", "storyforge-bot"])
    u = _linux_unit()
    if u.exists():
        u.unlink()
    _run(["systemctl", "--user", "daemon-reload"])
    print("  removed systemd unit")


def linux_running() -> bool:
    return (_run(["systemctl", "--user", "is-active", "storyforge-bot"]).stdout or "").strip() == "active"


# --------------------------------------------------------------------------- #
OS = platform.system()
IMPL = {
    "Windows": (win_install, win_uninstall, win_running),
    "Darwin": (mac_install, mac_uninstall, mac_running),
    "Linux": (linux_install, linux_uninstall, linux_running),
}


def main() -> int:
    if OS not in IMPL:
        print(f"[!] unsupported OS: {OS}")
        return 1
    install, uninstall, running = IMPL[OS]
    STATE.mkdir(parents=True, exist_ok=True)
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").lstrip("-").lower()

    if arg == "status":
        print(f"StoryForge bot on {OS}: {'RUNNING ✅' if running() else 'not running'}")
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

    print(f"Installing StoryForge autostart for {OS} ...")
    install()
    if OS == "Windows":          # the shortcut only fires at next login, so start now
        if not win_running():
            win_start()
    print("""
============================================================
 Done. The bot now starts silently at login — no window, ever.
 Talk to it in Discord: `!here` once, then just chat.

   check it:    python scripts/install_autostart.py --status
   remove it:   python scripts/install_autostart.py --uninstall

 Note: Ollama must also be running for the AI (it starts itself on
 Mac/Windows once installed). Idle cost of the bot is ~50MB RAM.
============================================================""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
