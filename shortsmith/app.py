"""The desktop entry point — starts the local server and opens a window.

Run it with `pythonw` (Windows) or from the .app/.command launcher and there is
no terminal involved at all: a real window appears with Shortsmith's icon in the
dock or taskbar.

The window is drawn by pywebview, which borrows the operating system's own
browser engine (WebKit on macOS, WebView2 on Windows). That costs about a
megabyte, where bundling Chromium the way an Electron app does would cost
roughly 150 MB — which would have undone the storage work entirely.

pywebview is optional on purpose. The interface is a local page either way, so
if the library is missing or the system webview refuses to start, the same app
opens in the default browser instead of failing. Nothing about the product
depends on the window.
"""
from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

from .utils import ROOT, log
from .webapp import serve

WINDOW_TITLE = "Shortsmith"
WIN_W, WIN_H = 1180, 820


def _icon_path() -> str | None:
    for name in ("icon.ico", "icon.png"):
        p = ROOT / "app" / name
        if p.exists():
            return str(p)
    return None


def _open_window(url: str) -> bool:
    """Try for a real app window. False means 'use the browser instead'."""
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    try:
        webview.create_window(WINDOW_TITLE, url, width=WIN_W, height=WIN_H,
                              min_size=(900, 640))
        # http_server=False: we already run our own server, and pywebview's
        # would bind a second port for nothing.
        webview.start(icon=_icon_path()) if _supports_icon() else webview.start()
        return True
    except Exception as exc:  # noqa: BLE001
        log("warn", f"app window unavailable ({exc}); falling back to the browser")
        return False


def _supports_icon() -> bool:
    """`icon=` is newer than some pywebview builds; don't crash on older ones."""
    try:
        import inspect

        import webview  # type: ignore
        return "icon" in inspect.signature(webview.start).parameters
    except Exception:  # noqa: BLE001
        return False


def _safe_stdio() -> None:
    """pythonw gives a process no console, so sys.stdout/stderr are None.

    The desktop shortcut launches with pythonw precisely so no black window
    appears — but that means any bare print() raises AttributeError and kills
    the app before the window ever opens, with nothing on screen to explain it.
    Point both streams at a log file instead, which also gives us somewhere to
    look when a double-click silently does nothing.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        logfile = ROOT / ".shortsmith_state" / "app.log"
        logfile.parent.mkdir(parents=True, exist_ok=True)
        fh = open(logfile, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = fh
        if sys.stderr is None:
            sys.stderr = fh
    except Exception:  # noqa: BLE001
        import io
        if sys.stdout is None:
            sys.stdout = io.StringIO()
        if sys.stderr is None:
            sys.stderr = io.StringIO()


def main(argv: list[str] | None = None) -> int:
    _safe_stdio()
    argv = argv if argv is not None else sys.argv[1:]
    browser_only = "--browser" in argv

    url, _httpd = serve(background=True)

    if not browser_only and _open_window(url):
        return 0                       # window closed = app closed

    # Fallback path: open the default browser and stay alive to serve it.
    webbrowser.open(url)
    print(f"Shortsmith is running at {url}")
    print("Close this window to quit.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
