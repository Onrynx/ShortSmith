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

import hmac
import json
import mimetypes
import os
import secrets
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
# --------------------------------------------------------------------------- #
# Who is allowed to talk to this server
# --------------------------------------------------------------------------- #
# Binding to 127.0.0.1 keeps the network out. It does NOT keep web pages out:
# a site the user is browsing can POST to http://127.0.0.1:8765 from their own
# browser. With a text/plain body that is a "simple request", so there is no
# CORS preflight to refuse — the request simply arrives and runs. The attacker
# cannot read the reply, but deleting every video needs no reply.
#
# And a site can get the reply too, via DNS rebinding: point evil.example at
# 127.0.0.1, and the browser treats this server as same-origin for evil.example.
#
# Three locks, because each one covers what the others miss:
#   Host     must be loopback — kills rebinding, which depends on a foreign name
#   Origin   if present, must be ours — kills the ordinary cross-site POST
#   token    a per-run secret, injected into the page we serve, required on
#            every /api/ call. Sent as a header, which cannot be set on a
#            cross-origin simple request at all.
SESSION_TOKEN = secrets.token_urlsafe(24)
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
MAX_BODY = 1 << 20          # 1 MB; no legitimate call is anywhere near this
# A thread per connection with no ceiling is a denial of service waiting for a
# bored script: sockets are cheap to open and each one costs us a thread and a
# stack. The cap is generous for one page (which keeps a handful of range
# requests in flight) and small enough that nothing can pile up unbounded.
MAX_CONNECTIONS = 48
# A connection that opens and then says nothing holds its thread forever
# without this. Reading a request is fast; keeping the socket alive between
# requests is what the longer idle window is for.
READ_TIMEOUT = 20.0
IDLE_TIMEOUT = 75.0
# How long an upload may go without sending a single byte before we give up on
# it. Generous for a slow disk or a slow link, fatal for an abandoned one.
UPLOAD_STALL_TIMEOUT = 90.0
_inflight = threading.BoundedSemaphore(MAX_CONNECTIONS)


def _host_is_loopback(value: str) -> bool:
    host = (value or "").strip()
    if host.startswith("["):                       # [::1]:8765
        host = host[1:].split("]")[0]
    elif host.count(":") == 1:                     # 127.0.0.1:8765
        host = host.split(":")[0]
    return host.lower() in _LOOPBACK


def render_state() -> dict:
    """Current render, in the shape the progress screen wants."""
    st = read_status(control.RENDER_SLOT) or {}
    stage = str(st.get("stage") or "").lower()
    done = st.get("pct") == 100.0 or "pending/" in str(st.get("message") or "")
    if stage in ("", "idle", "cancelled"):
        phase = "idle"
    elif stage == "failed":
        phase = "failed"
    elif stage == "awaiting":
        # Stopped on purpose between writing and rendering — not idle, not
        # working, and the screen has to say something different for it.
        phase = "awaiting"
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
            "check": m.get("self_check"),
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
            "check": m.get("self_check"),
        })
    return out


def _queue_state() -> dict:
    from .queue import state as qs
    return qs()


def _resume_state() -> dict | None:
    """A render that stopped early and could be carried on."""
    try:
        return control.resumable()
    except Exception:  # noqa: BLE001
        return None


def _gate_state() -> dict | None:
    """The script waiting to be read, if a run has stopped for one."""
    from .gate import waiting
    try:
        return waiting()
    except Exception:  # noqa: BLE001
        return None


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
        "queue": _queue_state(),
        "gate": _gate_state(),
        "resume": _resume_state(),
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


def _connection() -> dict:
    """How this machine is online, for the download warning. Never fatal."""
    try:
        from .network import status
        return status()
    except Exception:  # noqa: BLE001
        return {"kind": "unknown", "warn": False, "label": "your connection"}


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
        "connection": _connection(),
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

    # BaseHTTPRequestHandler applies this to the socket before reading.
    timeout = READ_TIMEOUT

    def log_message(self, fmt, *args):      # noqa: A003 - quiet by default
        pass

    def setup(self):        # noqa: D102
        super().setup()
        try:
            self.connection.settimeout(READ_TIMEOUT)
        except OSError:
            pass

    def handle(self):       # noqa: D102
        """One connection, counted against the ceiling.

        BoundedSemaphore rather than a plain one: releasing more than we
        acquired would silently raise the ceiling, and that is exactly the bug
        you never notice until the day it matters.
        """
        if not _inflight.acquire(blocking=False):
            # Raw bytes, not send_response(): nothing has been parsed yet, so
            # self.request_version does not exist and send_response() raises
            # on it. The old code threw inside the handler and dropped the
            # connection instead of answering — which is the one moment a
            # client most needs a real reply.
            try:
                self.wfile.write(b"HTTP/1.1 503 Service Unavailable\r\n"
                                 b"Retry-After: 1\r\n"
                                 b"Content-Length: 0\r\n"
                                 b"Connection: close\r\n\r\n")
                self.wfile.flush()
            except (OSError, AttributeError):
                pass
            self.close_connection = True
            return
        try:
            super().handle()
        finally:
            _inflight.release()

    def handle_one_request(self):   # noqa: D102
        # A socket that connects and then says nothing gets the short timeout;
        # only once it has actually made a request does it earn the longer
        # keep-alive window that a <video> element needs between range reads.
        try:
            self.connection.settimeout(
                IDLE_TIMEOUT if getattr(self, "_served", False) else READ_TIMEOUT)
        except OSError:
            pass
        super().handle_one_request()
        self._served = True

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

    def _body(self):
        """The request body as a dict, or an (error, code) pair to return.

        Returning {} for anything unparseable meant a malformed request ran the
        route with empty arguments instead of being refused, and a JSON array
        reached `body.get(...)` and raised — a 500 where the honest answer was
        "that is not a request this endpoint takes".
        """
        raw_len = self.headers.get("Content-Length")
        try:
            n = int(raw_len or 0)
        except (TypeError, ValueError):
            return ("length required", 411)
        if n < 0:
            return ("length required", 411)
        if n > MAX_BODY:
            return (f"body too large (limit {MAX_BODY} bytes)", 413)
        if n == 0:
            return {}
        try:
            raw = self.rfile.read(n)
        except OSError:
            return ("could not read the body", 400)
        if len(raw) != n:
            return ("body shorter than Content-Length", 400)
        try:
            parsed = json.loads(raw)
        except ValueError:
            return ("body is not valid JSON", 400)
        if not isinstance(parsed, dict):
            return ("body must be a JSON object", 400)
        return parsed

    def _caller_ok(self) -> bool:
        """Loopback Host, and an Origin/Referer that is ours or absent."""
        if not _host_is_loopback(self.headers.get("Host") or ""):
            return False
        # Checking only the hostname accepted "http://localhost:1" and any
        # scheme at all. The page we serve has exactly one origin; compare
        # against it rather than against a family of them.
        allowed = self._own_origins()
        for header in ("Origin", "Referer"):
            value = self.headers.get(header)
            if not value or value == "null":
                continue
            try:
                parsed = urlparse(value)
            except ValueError:
                return False
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "http" or host not in _LOOPBACK:
                return False
            port = parsed.port if parsed.port is not None else 80
            if (host, port) not in allowed:
                return False
        return True

    def _own_origins(self) -> set:
        """(host, port) pairs that are genuinely this server."""
        try:
            port = int(self.server.server_address[1])
        except Exception:  # noqa: BLE001
            return {(h, p) for h in _LOOPBACK for p in range(0)}
        return {(h, port) for h in _LOOPBACK}

    def _token_ok(self) -> bool:
        """The page we served knows the token; nothing else does.

        Accepted as a header for fetch(), or as ?k= for <img>/<video>/<audio>,
        which cannot send headers at all.
        """
        got = self.headers.get("X-Shortsmith-Token") or ""
        if not got:
            got = (parse_qs(urlparse(self.path).query).get("k") or [""])[0]
        # compare_digest refuses non-ASCII str and raises TypeError, which the
        # handler turned into a 500. Comparing bytes has no such restriction,
        # and a token that cannot even be encoded is simply wrong.
        try:
            supplied = str(got).encode("utf-8")
        except (UnicodeError, AttributeError):
            return False
        return hmac.compare_digest(supplied, SESSION_TOKEN.encode("ascii"))

    def _allowed(self, path: str) -> bool:
        if not self._caller_ok():
            return False
        return self._token_ok() if path.startswith("/api/") else True

    def do_OPTIONS(self):  # noqa: N802
        # Never answer a preflight. A cross-origin caller that needs one is,
        # by definition, not the page we served.
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _index(self) -> None:
        """index.html with this run's token substituted in.

        The token has to reach the page somehow, and the page is the one thing
        we can hand it to directly — anything fetched afterwards would need a
        way to authenticate itself first.
        """
        try:
            html = (APP_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            return self._json({"error": "index.html is missing"}, 500)
        body = html.replace("__SHORTSMITH_TOKEN__", SESSION_TOKEN).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        # The page never embeds anything remote, so say so.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "media-src 'self'; connect-src 'self'; "
                         "object-src 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

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
            # Three shapes, and the suffix one was wrong: "bytes=-3" asks for
            # the LAST three bytes, not the first four. The old code read the
            # empty left side as "start at 0" and the 3 as an end offset.
            try:
                spec = rng[6:].split(",")[0].strip()
                a, sep, b = spec.partition("-")
                if not sep:
                    raise ValueError("not a range")
                if not a:                       # bytes=-N  -> the last N bytes
                    n = int(b)
                    if n <= 0:
                        raise ValueError("empty suffix range")
                    start, end = max(0, size - n), size - 1
                else:
                    start = int(a)
                    end = min(int(b), size - 1) if b else size - 1
                partial = True
            except ValueError:
                partial = False
                start, end = 0, size - 1
            # An unsatisfiable range has its own status code; without this a
            # start past the end produced a zero-length 206 whose Content-Range
            # claimed otherwise, which browsers are entitled to find confusing.
            if partial and (start < 0 or start >= size or end < start):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
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

        if not self._allowed(p):
            return self._json({"error": "forbidden"}, 403)

        if p in ("/", "/index.html"):
            return self._index()
        if p == "/api/state":
            return self._json(app_state())
        if p.startswith("/api/voice/"):
            style = unquote(p.rsplit("/", 1)[-1])
            wav = voice_preview(style)
            if not wav:
                return self._json({"error": "voice preview unavailable"}, 503)
            return self._file(wav)
        if p == "/api/updates":
            from .updates import check
            return self._json(check(force="force" in (q or {})))
        if p == "/api/queue":
            from .queue import state as queue_state
            return self._json(queue_state())
        if p == "/api/boot":
            from .boot import state as boot_state
            return self._json(boot_state())
        if p == "/api/pull":
            from .setup_models import ready, state as setup_state
            data = setup_state()
            data["have"] = ready()
            return self._json(data)
        if p == "/api/machine":
            return self._json(machine_info())
        if p == "/api/connection":
            # Asked right before a download starts, so the answer is fresh
            # rather than whatever was true when Settings was opened.
            from .network import describe
            try:
                size = float((q.get("gb") or ["0"])[0])
            except (TypeError, ValueError):
                size = 0.0
            return self._json(describe(size))
        if p == "/api/clips":
            from .clips import free_space_gb, listing
            data = listing()
            data["free_gb"] = free_space_gb()
            return self._json(data)
        if p.startswith("/api/clipthumb/"):
            from .clips import thumb_path
            return self._file(thumb_path(unquote(p.rsplit("/", 1)[-1])))
        if p == "/api/events":
            return self._json({"events": control.events(20)})
        if p.startswith("/api/meta/"):
            iid = unquote(p.rsplit("/", 1)[-1])
            item = control.get_item(iid)
            if not item:
                # An id that names nothing — including one refused as unsafe —
                # is a 404, not an empty success. Returning blank metadata made
                # a rejected request look like a video with no title.
                return self._json({"error": "not found"}, 404)
            meta = item.get("metadata")
            if not meta:
                # Older videos predate per-video metadata; derive what we can
                # from the record rather than showing an empty panel.
                from .metadata import hashtags_from
                tags = item.get("tags") or []
                meta = {"titles": [item.get("title") or ""],
                        "title": item.get("title") or "",
                        "description": item.get("description") or "",
                        "hashtags": item.get("hashtags") or hashtags_from(tags),
                        "tags": tags, "caption": "", "source": "legacy"}
            return self._json(meta)
        if p.startswith("/api/script/"):
            iid = unquote(p.rsplit("/", 1)[-1])
            item = control.get_pending(iid) or control.find_item(iid, "approved")
            if not item:
                return self._json({"error": "not found"}, 404)
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
        # is_relative_to, not startswith: as strings, "<root>/app2/x" starts
        # with "<root>/app", so one neighbouring folder would be served too.
        if rel and cand.is_relative_to(APP_DIR.resolve()) and cand.is_file():
            return self._file(cand)
        return self._json({"error": "not found"}, 404)

    def _route_post(self):
        p = urlparse(self.path).path
        if not self._allowed(p):
            return self._json({"error": "forbidden"}, 403)

        if p == "/api/clips/upload":
            # Raw bytes, not multipart: a background clip is routinely
            # gigabytes and this is handled BEFORE _body(), which would
            # otherwise try to read the whole thing into memory and refuse it
            # for being over the JSON limit.
            from .clips import MAX_CLIP_BYTES, save_stream
            try:
                declared = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                return self._json({"error": "length required"}, 411)
            if declared <= 0:
                return self._json({"error": "length required"}, 411)
            if declared > MAX_CLIP_BYTES:
                return self._json({"error": "that file is too large"}, 413)
            name = unquote(self.headers.get("X-Clip-Name") or "")
            # A socket timeout is PER READ, not for the whole transfer. Setting
            # it to half an hour meant a client that promised 2 GB and then
            # went quiet held a thread — and one of the 48 connection slots —
            # for that entire half hour. A real upload never pauses this long
            # between chunks, however big it is; silence this long means the
            # other end is gone.
            try:
                self.connection.settimeout(UPLOAD_STALL_TIMEOUT)
            except OSError:
                pass
            r = save_stream(name, self.rfile, declared)
            return self._json(r, 200 if r.get("ok") else 400)

        body = self._body()
        if isinstance(body, tuple):
            message, code = body
            return self._json({"error": message}, code)

        if p == "/api/make":
            count = body.get("count") or 1
            try:
                count = max(1, min(10, int(count)))
            except (TypeError, ValueError):
                count = 1
            if control.render_in_progress() and count == 1:
                return self._json({"error": "a render is already running"}, 409)
            brief = str(body.get("brief") or "").strip()
            secs = body.get("seconds")
            voice = body.get("voice") if body.get("voice") in ("adam", "squeaky") else "adam"
            mode = body.get("mode") if body.get("mode") in ("guided", "express", "auto") else "express"
            channel = body.get("channel") or default_channel()
            try:
                secs = float(secs) if secs else None
            except (TypeError, ValueError):
                secs = None
            # Anything but a single immediate render goes through the queue,
            # which also gives each variation its own angle on the brief.
            if count > 1 or control.render_in_progress():
                from .queue import add as queue_add
                r = queue_add(brief, secs, voice, channel, count, mode)
                return self._json(r, 200 if r.get("ok") else 400)
            r = control.run_now(channel, control.RENDER_SLOT, False,
                                voice=voice, topic=brief, seconds=secs, mode=mode)
            return self._json(r, 200 if r.get("ok") else 400)

        if p == "/api/resume":
            r = control.resume_render(str(body.get("run_id") or ""))
            return self._json(r, 200 if r.get("ok") else 400)
        if p == "/api/discard":
            r = control.discard_resumable(str(body.get("run_id") or ""))
            return self._json(r, 200 if r.get("ok") else 400)

        if p == "/api/gate":
            # go / rewrite / drop for the script a run is waiting on.
            from .gate import decide
            r = decide(str(body.get("run_id") or ""),
                       str(body.get("action") or ""),
                       str(body.get("note") or ""))
            return self._json(r, 200 if r.get("ok") else 400)

        if p == "/api/queue/cancel":
            # int() on whatever arrived threw for anything that was not a
            # number — a string, a dict, a list — and the handler turned that
            # into a 500. An id that is not an id means "cancel everything
            # waiting", which is what omitting it already meant.
            from .queue import cancel as queue_cancel
            raw_id = body.get("id")
            try:
                jid = int(raw_id) if raw_id not in (None, "", False) else None
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "that is not a job id"}, 400)
            return self._json(queue_cancel(jid))
        if p == "/api/queue/clear":
            from .queue import clear_finished
            return self._json(clear_finished())
        if p == "/api/cancel":
            return self._json(control.cancel_render())
        if p == "/api/approve":
            return self._json(control.approve(str(body.get("id") or "")))
        if p == "/api/reject":
            return self._json(control.reject(str(body.get("id") or "")))
        if p == "/api/bulk":
            # One request for a whole selection: the UI lets you tick several
            # videos, and firing N separate calls would half-apply on an error.
            raw_ids = body.get("ids")
            # Iterating whatever arrived raised TypeError on a number and
            # silently walked the characters of a string, which would have
            # turned "abc" into three deletions of "a", "b" and "c".
            if raw_ids is None:
                raw_ids = []
            if not isinstance(raw_ids, list):
                return self._json({"error": "ids must be a list"}, 400)
            if len(raw_ids) > 500:
                return self._json({"error": "too many ids in one request"}, 400)
            ids = [str(i) for i in raw_ids if isinstance(i, str) and i.strip()]
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
            # Only tags from our own menu. Downloading is a multi-gigabyte,
            # long-running action and the name should never be free text.
            from .model_profiles import CATALOG
            entry = next((e for e in CATALOG if e["tag"] == model), None)
            if entry is None:
                return self._json({"error": "unknown model"}, 400)
            # The warning covers everything that will be fetched, not only the
            # part the user picked. The voice and caption models used to go out
            # over a metered connection without being counted or mentioned.
            from .network import describe
            from .setup_models import start as start_setup, total_gb
            conn = describe(total_gb(model))
            if conn.get("warn") and not body.get("confirm"):
                return self._json({"ok": False, "needs_confirm": True,
                                   "message": conn["message"],
                                   "connection": conn}, 409)
            return self._json(start_setup(model))

        if p == "/api/update":
            from .updates import apply_update
            return self._json(apply_update())

        if p == "/api/meta":
            # With a "meta" body, save an edit (choosing one of the title
            # options). Without one, write the metadata again from scratch.
            iid = str(body.get("id") or "")
            item = control.get_item(iid)
            if not item:
                return self._json({"error": "not found"}, 404)
            if isinstance(body.get("meta"), dict):
                from .metadata import sanitize
                meta = sanitize(body["meta"])
                if not meta:
                    return self._json({"error": "that is not metadata"}, 400)
                control.save_metadata(iid, meta)
                return self._json(meta)
            from .llm import select_model
            from .metadata import build as build_meta
            meta = build_meta({"title": item.get("title"),
                               "tags": item.get("tags"),
                               "scenes": item.get("scenes")},
                              select_model(False))
            control.save_metadata(iid, meta)
            return self._json(meta)

        if p == "/api/clips/delete":
            from .clips import remove
            r = remove(str(body.get("name") or ""))
            return self._json(r, 200 if r.get("ok") else 400)

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


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that will not share its port.

    HTTPServer sets allow_reuse_address, which on Windows means SO_REUSEADDR —
    and there that permits a SECOND process to bind the same address and port,
    with no defined rule about which one gets a given connection. On Linux the
    same flag only allows rebinding a socket in TIME_WAIT, which is what we
    actually want, so the behaviour is split by platform rather than disabled
    everywhere.
    """
    allow_reuse_address = os.name != "nt"
    daemon_threads = True
    request_queue_size = 32


def serve(port: int | None = None, background: bool = False):
    """Start the local server. Returns (url, httpd)."""
    # Sweep stale companions of finished videos once at startup. Cheap (a
    # directory listing), and it means a leftover from a removed feature is
    # gone the next time the app opens rather than sitting there for months.
    try:
        r = control.tidy()
        if r.get("count"):
            log("info", f"startup tidy: removed {r['count']} stale file(s), "
                        f"{r['freed_mb']} MB")
    except Exception as exc:  # noqa: BLE001
        log("warn", f"startup tidy skipped: {exc}")
    port = port or _free_port()
    httpd = _Server((HOST, port), _Handler)
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
