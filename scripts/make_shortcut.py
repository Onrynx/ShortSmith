"""Create a proper double-clickable launcher with StoryForge's icon.

A .bat file cannot carry an icon — Windows shows it with a generic gear, which
is exactly what we don't want. A SHORTCUT can, so on Windows this writes a .lnk
pointing at pythonw (no console window) with icon.ico attached. On macOS it
builds a real .app bundle, which is just a folder with a launch script, an
Info.plist and an .icns inside.

Run:  python scripts/make_shortcut.py            -> Desktop
      python scripts/make_shortcut.py --here     -> project folder
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "StoryForge"


def _target_dir(argv: list[str]) -> Path:
    if "--here" in argv:
        return ROOT
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else ROOT


def windows(dest: Path) -> Path:
    """A .lnk running pythonw (windowless) with our icon."""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    link = dest / f"{NAME}.lnk"
    icon = ROOT / "app" / "icon.ico"
    ps = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}')
$s.TargetPath = '{pyw}'
$s.Arguments = '-m storyforge.app'
$s.WorkingDirectory = '{ROOT}'
$s.IconLocation = '{icon}'
$s.Description = 'StoryForge - make a Short'
$s.Save()
"""
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   check=True, capture_output=True)
    return link


def macos(dest: Path) -> Path:
    """A real .app bundle — a folder the Finder treats as an application."""
    app = dest / f"{NAME}.app"
    macos_dir = app / "Contents" / "MacOS"
    res_dir = app / "Contents" / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    res_dir.mkdir(parents=True, exist_ok=True)

    launcher = macos_dir / NAME
    launcher.write_text(
        "#!/bin/bash\n"
        f'cd "{ROOT}"\n'
        f'exec "{sys.executable}" -m storyforge.app\n',
        encoding="utf-8")
    launcher.chmod(0o755)

    icns = ROOT / "app" / "icon.icns"
    if icns.exists():
        (res_dir / "icon.icns").write_bytes(icns.read_bytes())

    info = {
        "CFBundleName": NAME,
        "CFBundleDisplayName": NAME,
        "CFBundleExecutable": NAME,
        "CFBundleIdentifier": "local.storyforge.app",
        "CFBundleIconFile": "icon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "2.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    }
    with (app / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh)
    return app


def linux(dest: Path) -> Path:
    entry = dest / f"{NAME}.desktop"
    entry.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={NAME}\nComment=Make a Short\n"
        f'Exec="{sys.executable}" -m storyforge.app\n'
        f"Path={ROOT}\n"
        f"Icon={ROOT / 'app' / 'icon.png'}\n"
        "Terminal=false\nCategories=AudioVideo;\n",
        encoding="utf-8")
    entry.chmod(0o755)
    return entry


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not (ROOT / "app" / "icon.png").exists():
        print("icon missing — run: python scripts/make_icon.py")
        return 1
    dest = _target_dir(argv)
    try:
        if os.name == "nt":
            made = windows(dest)
        elif sys.platform == "darwin":
            made = macos(dest)
        else:
            made = linux(dest)
    except Exception as exc:  # noqa: BLE001
        print(f"could not create the launcher: {exc}")
        return 1
    print(f"created {made}")
    print("Double-click it to open StoryForge. No terminal, no commands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
