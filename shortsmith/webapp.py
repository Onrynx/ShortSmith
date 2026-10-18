"""The local app server — a thin HTTP layer over control.py.

Shortsmith's whole product surface already exists as plain functions in
control.py, with no interface assumptions baked in. This module exposes those
functions over HTTP so a local page can drive them, which is what makes an app
possible without rewriting any of the pipeline.

Design rules, in order of importance:

1. LOCALHOST ONLY. The socket binds to 127.0.0.1, never 0.0.0.0. Nothing on the
   user's Wi-Fi, nothing on the internet, and no other device can reach it. It
   is two processes on one machine talking to each other, not a website.

2. NO NEW DEPENDENCIES. Everything here is Python's standard library, so the
   app adds nothing to the 237 MB the pipeline already needs.

3. NEVER CRASH THE SERVER. A handler that raises returns JSON with the error
   rather than killing the process — the window closing because a thumbnail was
   missing would be a terrible way to lose a render in progress.

Video is served with HTTP range support, because without it browsers refuse to
seek and some will not play an mp4 at all.
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import control
from .config import default_channel, list_channels, load_channel
from .utils import ROOT, STATE_DIR, ensure_dir, log, read_status

APP_DIR = ROOT / "app"
HOST = "127.0.0.1"          # never 0.0.0.0 — see rule 1
DEFAULT_PORT = 8765

_STAGES = ["script", "voice", "canvas", "cutter", "herald"]
_STAGE_TEXT = {
    "script": "Writing the story",
    "voice":  "Recording the narration",
    "canvas": "Building the video",
    "cutter": "Cutting it together",
    "herald": "Finishing up",
}


# --------------------------------------------------------------------------- #
# What the UI needs to know
# --------------------------------------------------------------------------- #
def render_state() -> dict:
    """Current render, in the shape the progress screen wants."""
    st = read_status(control.RENDER_SLOT) or {}
    stage = str(st.get("stage") or "").lower()
    done = st.get("pct") == 100.0 or "pending/" in str(st.get("message") or "")
    if stage in ("", "idle", "cancelled"):
        phase = "idle"
    elif stage == "failed":
        phase = "failed"
    elif done:
        phase = "done"
    else:
        phase = "running"
    return {
        "phase": phase,
        "stage": stage,
        "step": (_STAGES.index(stage) + 1) if stage in _STAGES else None,
        "steps": len(_STAGES),
        "label": _STAGE_TEXT.get(stage, stage or "Idle"),
        "message": st.get("message") or "",
        "title": st.get("title"),
        "logline": st.get("logline"),
        "structure": st.get("structure"),
        "scenes_written": st.get("scenes_written"),
        "scenes_total": st.get("scenes_total"),
        "words_written": st.get("words_written"),
        "opening": st.get("opening"),
        "run_id": st.get("run_id"),
    }


def pending_items() -> list[dict]:
    out = []
    for i, m in enumerate(control.list_pending()):
        out.append({
            "n": i + 1,
            "id": m.get("id"),
            "title": m.get("title") or "Untitled",
            "channel": m.get("channel") or "",
            "seconds": round(m.get("runtime_sec") or 0),
            "scenes": len(m.get("scenes") or []),
            "structure": (m.get("plan") or {}).get("structure") if isinstance(m.get("plan"), dict) else None,
        })
    return out


def approved_items() -> list[dict]:
    out = []
    for m in control.history(limit=200):
        out.append({
            "id": m.get("id"),
            "title": m.get("title") or "Untitled",
            "channel": m.get("channel") or "",
            "seconds": round(m.get("runtime_sec") or 0),
            "scenes": len(m.get("scenes") or []),
            "when": (m.get("approved_at") or m.get("created") or "")[:10],
        })
    return out


def app_state() -> dict:
    """One call the UI can poll for everything on screen."""
    try:
        s = control.status()
        pending_count = s.get("pending_count", 0)
    except Exception:  # noqa: BLE001
        pending_count = 0
    return {
        "render": render_state(),
        "pending": pending_items(),
        "pending_count": pending_count,
        "channels": list_channels(),
        "default_channel": default_channel(),
        "busy": control.render_in_progress(),
        "approved": approved_items(),
    }


def installed_models() -> list[str]:
    """Models Ollama already has, so the menu can say Download vs Ready."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4) as r:
            data = _json.loads(r.read().decode("utf-8", "replace"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def machine_info() -> dict:
    """Hardware + model menu. Read from cache; only scans on first ever call."""
    from .hardware import scan, summary
    from .model_profiles import BASE_INSTALL_GB, catalog_for, recommend
    hw = scan()
    rec = recommend(hw)
    return {
        "hardware": hw,
        "summary": summary(hw),
        "recommended": rec["recommended"],
        "profile": rec["profile"],
        "options": catalog_for(hw),
        "base_install_gb": BASE_INSTALL_GB,
        "installed": installed_models(),
        "pull": pull_state(),
    }


# --------------------------------------------------------------------------- #
# Model download — so choosing a model never needs a terminal
# --------------------------------------------------------------------------- #
# Ollama exposes a streaming pull API. Driving it from here is what lets the
# setup screen say "pick one and press Download" instead of telling somebody to
# type `ollama pull qwen2.5:7b`. Only the model the user picked is fetched.
_PULL: dict = {"model": None, "status": "idle", "percent": 0,
               "done_gb": 0.0, "total_gb": 0.0, "error": None}


def pull_state() -> dict:
    return dict(_PULL)


def _pull_worker(model: str) -> None:
    import json as _json
    import urllib.request
    from .llm import ollama_base_url

    base = ollama_base_url() if callable(globals().get("ollama_base_url", None)) else None
    base = base or "http://localhost:11434"
    _PULL.update(model=model, status="downloading", percent=0,
                 done_gb=0.0, total_gb=0.0, error=None)
    try:
        req = urllib.request.Request(
            f"{base}/api/pull",
            data=_json.dumps({"model": model, "stream": True}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=None) as resp:
            for raw in resp:
                if not raw.strip():
                    continue
                try:
                    msg = _json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if msg.get("error"):
                    _PULL.update(status="failed", error=str(msg["error"]))
                    return
                done, total = msg.get("completed"), msg.get("total")
                if done and total:
                    _PULL.update(percent=round(done / total * 100, 1),
                                 done_gb=round(done / 1024 ** 3, 2),
                                 total_gb=round(total / 1024 ** 3, 2))
                _PULL["status"] = str(msg.get("status") or "downloading")
        _PULL.update(status="done", percent=100.0)
        log("info", f"model {model} downloaded")
    except Exception as exc:  # noqa: BLE001
        _PULL.update(status="failed", error=str(exc))
        log("warn", f"model pull failed: {exc}")


def start_pull(model: str) -> dict:
    if _PULL.get("status") == "downloading":
        return {"ok": False, "error": "a download is already running"}
    threading.Thread(target=_pull_worker, args=(model,), daemon=True).start()
    return {"ok": True, "model": model}


# --------------------------------------------------------------------------- #
# Voice preview — hear a narrator before committing 12 minutes to one
# --------------------------------------------------------------------------- #
_PREVIEW_LINE = ("He said he'd pay me back on Friday. That was eleven months ago.")


def voice_preview(style: str) -> Path | None:
    """Synthesise one short line in the chosen style, cached per style."""
    from .config import load_channel
    from .nodes.voice import (VOICE_STYLES, _pitch_up, synth_kokoro,
                              voice_speed)
    style = style if style in VOICE_STYLES else "adam"
    out = ensure_dir(STATE_DIR / "previews") / f"{style}.wav"
    if out.exists() and out.stat().st_size > 1000:
        return out
    cfg = load_channel(default_channel())
    spec = VOICE_STYLES[style]
    speed = round(voice_speed(cfg) * (spec["speed"] / VOICE_STYLES["adam"]["speed"]), 3)
    try:
        synth_kokoro(_PREVIEW_LINE, cfg.get("voice", "am_adam"), out, speed)
        _pitch_up(out, spec["pitch"])
        return out
    except Exception as exc:  # noqa: BLE001
        log("warn", f"voice preview failed: {exc}")
        return None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    server_version = "Shortsmith"
    # HTTP/1.1 is required for video. The default (1.0) closes the connection
    # after every response, and a <video> element issues dozens of range
    # requests — browsers treat those mid-playback closes as a stall, so clips
    # played for about a second and stopped. Keep-alive fixes it. Every reply
    # here sends an accurate Content-Length, which 1.1 requires.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):      # noqa: A003 - quiet by default
        pass

    # ---- helpers ---- #
    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass            # client went away mid-reply; nothing to do

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            return {}

    def _file(self, path: Path, download: bool = False) -> None:
        """Send a file, honouring Range so browsers can seek video."""
        if not path or not path.exists():
            return self._json({"error": "not found"}, 404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng and rng.startswith("bytes="):
            try:
                a, _, b = rng[6:].partition("-")
                if a:
                    start = int(a)
                if b:
                    end = min(int(b), size - 1)
                partial = True
            except ValueError:
                partial = False
        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", formatdate(path.stat().st_mtime, usegmt=True))
        if path.suffix.lower() in (".html", ".css", ".js"):
            # The webview will happily keep serving yesterday's interface
            # otherwise, which looks exactly like "the fix didn't work".
            self.send_header("Cache-Control", "no-store, must-revalidate")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{path.name}"')
        self.end_headers()
        try:
            with path.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except OSError:
            # Browsers abort video streams constantly — every seek, and again
            # when <video preload> has read enough. On Windows that surfaces as
            # ConnectionAbortedError, which is not an error condition here.
            pass

    # ---- routes ---- #
    def _guard(self, fn, label: str) -> None:
        """Run a route; never let it kill the server or double-fault."""
        try:
            fn()
        except OSError:
            return          # connection dropped — there is nobody to reply to
        except Exception as exc:  # noqa: BLE001
            log("warn", f"webapp {label} {self.path}: {exc}")
            try:
                self._json({"error": str(exc)}, 500)
            except OSError:
                pass

    def do_GET(self):  # noqa: N802
        self._guard(self._route_get, "GET")

    def do_POST(self):  # noqa: N802
        self._guard(self._route_post, "POST")

    def _route_get(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)

        if p in ("/", "/index.html"):
            return self._file(APP_DIR / "index.html")
        if p == "/api/state":
            return self._json(app_state())
        if p.startswith("/api/voice/"):
            style = unquote(p.rsplit("/", 1)[-1])
            wav = voice_preview(style)
            if not wav:
                return self._json({"error": "voice preview unavailable"}, 503)
            return self._file(wav)
        if p == "/api/pull":
            return self._json(pull_state())
        if p == "/api/machine":
            return self._json(machine_info())
        if p == "/api/events":
            return self._json({"events": control.events(20)})
        if p.startswith("/api/script/"):
            iid = unquote(p.rsplit("/", 1)[-1])
            item = control.get_pending(iid) or control.find_item(iid, "approved") or {}
            return self._json({
                "title": item.get("title"),
                "scenes": [{"narration": sc.get("narration"),
                            "purpose": sc.get("purpose")}
                           for sc in (item.get("scenes") or [])],
            })
        if p.startswith("/api/video/"):
            return self._file(control.media_path(unquote(p.rsplit("/", 1)[-1]), "video"))
        if p.startswith("/api/thumb/"):
            return self._file(control.media_path(unquote(p.rsplit("/", 1)[-1]), "thumb"))
        # any other static file under app/
        rel = p.lstrip("/")
        cand = (APP_DIR / rel).resolve()
        if rel and str(cand).startswith(str(APP_DIR.resolve())) and cand.is_file():
            return self._file(cand)
        return self._json({"error": "not found"}, 404)

    def _route_post(self):
        p = urlparse(self.path).path
        body = self._body()

        if p == "/api/make":
            if control.render_in_progress():
                return self._json({"error": "a render is already running"}, 409)
            brief = str(body.get("brief") or "").strip()
            secs = body.get("seconds")
            voice = body.get("voice") if body.get("voice") in ("adam", "squeaky") else "adam"
            channel = body.get("channel") or default_channel()
            try:
                secs = float(secs) if secs else None
            except (TypeError, ValueError):
                secs = None
            r = control.run_now(channel, control.RENDER_SLOT, False,
                                voice=voice, topic=brief, seconds=secs)
            return self._json(r, 200 if r.get("ok") else 400)

        if p == "/api/cancel":
            return self._json(control.cancel_render())
        if p == "/api/approve":
            return self._json(control.approve(str(body.get("id") or "")))
        if p == "/api/reject":
            return self._json(control.reject(str(body.get("id") or "")))
        if p == "/api/bulk":
            # One request for a whole selection: the UI lets you tick several
            # videos, and firing N separate calls would half-apply on an error.
            ids = [str(i) for i in (body.get("ids") or []) if i]
            action = str(body.get("action") or "")
            if action not in ("approve", "reject", "delete"):
                return self._json({"error": "unknown action"}, 400)
            done, failed = [], []
            for iid in ids:
                try:
                    if action == "approve":
                        r = control.approve(iid)
                    elif action == "reject":
                        r = control.reject(iid)
                    else:
                        r = control.delete_item(iid, "approved")
                    (done if r.get("ok") else failed).append(iid)
                except Exception:  # noqa: BLE001
                    failed.append(iid)
            return self._json({"ok": not failed, "done": len(done),
                               "failed": len(failed)})

        if p == "/api/pull":
            model = str(body.get("model") or "").strip()
            if not model:
                return self._json({"error": "no model given"}, 400)
            return self._json(start_pull(model))

        if p == "/api/rescan":
            from .hardware import rescan
            return self._json(rescan())
        if p == "/api/reveal":
            # open the output folder in the OS file manager
            target = control.media_path(str(body.get("id") or ""), "video")
            folder = str(Path(target).parent) if target else str(ROOT / "output")
            try:
                if os.name == "nt":
                    os.startfile(folder)  # noqa: S606
                else:
                    import subprocess
                    subprocess.Popen(
                        ["open" if os.uname().sysname == "Darwin" else "xdg-open", folder])
                return self._json({"ok": True})
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": str(exc)})
        return self._json({"error": "not found"}, 404)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def _free_port(preferred: int = DEFAULT_PORT) -> int:
    """Use the preferred port when it is free, else let the OS pick one."""
    for port in (preferred, 0):
        try:
            with socket.socket() as s:
                s.bind((HOST, port))
                return s.getsockname()[1]
        except OSError:
            continue
    return preferred


def serve(port: int | None = None, background: bool = False):
    """Start the local server. Returns (url, httpd)."""
    port = port or _free_port()
    httpd = ThreadingHTTPServer((HOST, port), _Handler)
    httpd.daemon_threads = True
    url = f"http://{HOST}:{port}/"
    log("info", f"Shortsmith app listening on {url} (this machine only)")
    if background:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
    else:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.shutdown()
    return url, httpd


if __name__ == "__main__":      # python -m shortsmith.webapp
    import webbrowser
    url, httpd = serve(background=True)
    print(f"Shortsmith running at {url}")
    webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nstopped")
