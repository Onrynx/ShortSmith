"""Who writes the stories: this computer, or an online AI with your own key.

Writing on this computer, with Ollama, is the default and stays the default.
Some laptops are too slow for it to be pleasant -- an 8 GB laptop can spend
most of an hour on one script -- so a person may instead give Shortsmith a key
for an online AI and have only the writing done there.

Any company will do, as long as it offers the OpenAI-style chat API, which is
nearly all of them: Groq, Google Gemini, OpenRouter, OpenAI, Mistral,
DeepSeek, and anything else that says "OpenAI-compatible" -- including a
program on this computer such as LM Studio. There is one client, not one per
company, so a company that is not listed works too.

What goes where, said plainly because the app makes promises about it:

  * Online writing is never a fallback. It is used only after the person
    chose it in Settings and gave a key, and it stops when they choose local.
  * Only the writing goes online: the idea, the plan and the script. The
    voice, the captions, the video and the gameplay footage are made here.
  * The key is kept outside the Shortsmith folder, so zipping the folder to
    give to a friend never gives them the key. On Windows it is encrypted for
    the signed-in user (DPAPI); on macOS and Linux it is a file only this user
    can read, which is how SSH keys and cloud tools keep theirs.
  * The key never goes back to the window, never into a log, and never into
    a render's saved state. It is read at the moment of each request, and
    only ever sent to the address saved beside it.

Nothing here needs a library the app does not already ship, so it arrives in
an ordinary update rather than a new download.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__
from .utils import log

# --------------------------------------------------------------------------- #
# The companies offered by name
# --------------------------------------------------------------------------- #
# `prefer` only decides which model is selected first from the company's own
# list of models; the person can pick any other. Matched as substrings, so a
# newer version of the same family still matches. "free" marks the companies
# with a free allowance, as far as the published comparisons agree.
PRESETS = [
    {"id": "groq", "label": "Groq", "free": True,
     "base_url": "https://api.groq.com/openai/v1",
     "key_url": "https://console.groq.com/keys",
     "prefer": ["gpt-oss-120b", "llama-3.3-70b", "kimi-k2", "70b"]},
    {"id": "gemini", "label": "Google Gemini", "free": True,
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "key_url": "https://aistudio.google.com/apikey",
     "prefer": ["flash"]},
    {"id": "openrouter", "label": "OpenRouter", "free": True,
     "base_url": "https://openrouter.ai/api/v1",
     "key_url": "https://openrouter.ai/keys",
     "prefer": ["llama-3.3-70b-instruct:free", "deepseek-chat", "gpt-oss-120b:free",
                ":free"]},
    {"id": "openai", "label": "OpenAI", "free": False,
     "base_url": "https://api.openai.com/v1",
     "key_url": "https://platform.openai.com/api-keys",
     "prefer": ["gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini", "mini"]},
    {"id": "mistral", "label": "Mistral", "free": False,
     "base_url": "https://api.mistral.ai/v1",
     "key_url": "https://console.mistral.ai/api-keys",
     "prefer": ["mistral-medium-latest", "mistral-small-latest", "latest"]},
    {"id": "deepseek", "label": "DeepSeek", "free": False,
     "base_url": "https://api.deepseek.com/v1",
     "key_url": "https://platform.deepseek.com/api_keys",
     "prefer": ["deepseek-chat"]},
    {"id": "other", "label": "Another service (OpenAI-compatible)", "free": False,
     "base_url": "", "key_url": "", "prefer": []},
]

# Models a company lists that cannot write a story: speech, pictures,
# embeddings, safety filters. Matched as substrings of the model's name.
_NOT_WRITERS = ("embed", "whisper", "tts", "dall-e", "image", "moderation",
                "audio", "transcri", "guard", "rerank", "realtime", "search",
                "aqa", "imagen", "veo", "speech", "computer-use", "orpheus",
                "playai", "lyria")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": {}}
_keymemo: dict = {}         # sealed blob -> the key, so it is unlocked once
_ADAPT: dict = {}           # (address, model) -> adjustments the service asked for
_VERIFIED: dict = {}        # (address, model, key digest) -> when it last answered


class WriterUnavailable(RuntimeError):
    """The online writer cannot be used right now, for a reason a person can fix.

    Raised instead of an ordinary error so the render stops and says why. An
    ordinary failure in the writing stage falls through to the built-in
    sample story -- for a wrong key that would have produced a video nobody
    asked for, with no hint that the key was the problem.
    """


# --------------------------------------------------------------------------- #
# Where the setting lives
# --------------------------------------------------------------------------- #
def _home() -> Path:
    """The per-user settings folder, outside the Shortsmith folder."""
    override = os.environ.get("SHORTSMITH_HOME", "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Shortsmith"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Shortsmith"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "shortsmith"


def _file() -> Path:
    return _home() / "writer.json"


def load() -> dict:
    """The saved choice, without the key unlocked. {} when nothing is chosen."""
    p = _file()
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] == mtime:
            return dict(_cache["data"])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    with _lock:
        _cache.update(mtime=mtime, data=data)
    return dict(data)


def _write(data: dict) -> None:
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(home, 0o700)
        except OSError:
            pass
    tmp = home / "writer.json.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, _file())
    if os.name != "nt":
        try:
            os.chmod(_file(), 0o600)
        except OSError:
            pass
    with _lock:
        _cache["mtime"] = None


# --------------------------------------------------------------------------- #
# The key
# --------------------------------------------------------------------------- #
def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows' own per-user encryption. Only this account can undo it."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    src, _keep1 = blob(data)
    entropy, _keep2 = blob(b"Shortsmith writer key")
    out = Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN: never put up a prompt of its own.
    if not fn(ctypes.byref(src), None, ctypes.byref(entropy), None, None, 0x1,
              ctypes.byref(out)):
        raise OSError(f"Windows refused to {'lock' if protect else 'unlock'} "
                      f"the key ({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _seal(secret: str) -> dict:
    if os.name == "nt":
        return {"kind": "dpapi", "data": base64.b64encode(
            _dpapi(secret.encode("utf-8"), True)).decode("ascii")}
    return {"kind": "plain", "data": secret}


def key(cfg: dict | None = None) -> str:
    """The saved key, or "" -- including when it cannot be unlocked here.

    A key locked on another computer or by another Windows user cannot be
    opened, which is the point; that reads as "no key" and the person is
    asked for it again.
    """
    blob = (load() if cfg is None else cfg).get("key")
    if not isinstance(blob, dict):
        return ""
    data = str(blob.get("data") or "")
    if blob.get("kind") == "plain":
        return data
    if blob.get("kind") != "dpapi" or os.name != "nt" or not data:
        return ""
    if data not in _keymemo:
        try:
            _keymemo[data] = _dpapi(base64.b64decode(data), False).decode("utf-8")
        except Exception:  # noqa: BLE001
            _keymemo[data] = ""
    return _keymemo[data]


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# What is chosen
# --------------------------------------------------------------------------- #
def preset(pid) -> dict:
    return next((p for p in PRESETS if p["id"] == pid), {})


def _host(base: str) -> str:
    return urllib.parse.urlsplit(base or "").hostname or ""


def label_of(cfg: dict | None = None) -> str:
    cfg = load() if cfg is None else cfg
    pre = preset(cfg.get("provider"))
    if pre and pre["id"] != "other":
        return pre["label"]
    return _host(cfg.get("base_url") or "") or "the online service"


def ready(cfg: dict | None = None) -> bool:
    """Online writing is chosen and has everything it needs."""
    cfg = load() if cfg is None else cfg
    return bool(cfg.get("mode") == "online" and cfg.get("base_url")
                and cfg.get("model") and key(cfg))


def is_online_model(tag: str) -> bool:
    cfg = load()
    return bool(tag) and cfg.get("mode") == "online" and tag == cfg.get("model")


def summary() -> dict:
    """What the sidebar says about where the writing happens."""
    cfg = load()
    on = ready(cfg)
    return {"online": on, "label": label_of(cfg) if on else "",
            "model": cfg.get("model", "") if on else ""}


def public_state() -> dict:
    """Everything the Settings page shows. Never the key itself."""
    cfg = load()
    return {
        "mode": cfg.get("mode") or "", "provider": cfg.get("provider") or "",
        "base_url": cfg.get("base_url") or "", "model": cfg.get("model") or "",
        "has_key": bool(key(cfg)), "key_hint": cfg.get("key_hint") or "",
        "label": label_of(cfg), "ready": ready(cfg), "encrypted": os.name == "nt",
        "presets": [{k: p[k] for k in ("id", "label", "base_url", "key_url", "free")}
                    for p in PRESETS],
    }


# --------------------------------------------------------------------------- #
# Talking to the service
# --------------------------------------------------------------------------- #
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects.

    urllib follows them and carries the Authorization header along, so a
    redirect to another host would hand that host the key. These APIs have
    no reason to redirect; one that does gets an error, not the key.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def clean_base(url) -> str:
    """The service's address, checked. Raises ValueError with a plain reason."""
    u = str(url or "").strip()
    if not u:
        raise ValueError("Type the service's address, for example "
                         "https://api.example.com/v1")
    u = re.sub(r"/+$", "", u)
    u = re.sub(r"/(chat/completions|models)$", "", u)
    p = urllib.parse.urlsplit(u)
    host = (p.hostname or "").lower()
    if p.scheme not in ("https", "http") or not host:
        raise ValueError("That is not a web address. It should start with https://")
    if p.username or p.password or p.query or p.fragment:
        raise ValueError("Use the plain address, without a password, ? or # in it.")
    if p.scheme == "http" and host not in _LOCAL_HOSTS:
        raise ValueError("Only https:// addresses are allowed, so the key is never "
                         "sent unencrypted. (http:// is fine for a program on this "
                         "computer.)")
    return u


def _request(method: str, url: str, secret: str, body=None, timeout: float = 60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"Shortsmith/{__version__}",
    })
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw, status, headers = r.read().decode("utf-8", "replace"), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        status, headers = e.code, e.headers
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = raw
    return status, {str(k).lower(): v for k, v in (headers or {}).items()}, parsed


def _message(parsed) -> str:
    """The service's own explanation of an error, whatever shape it came in."""
    if isinstance(parsed, list) and parsed:
        return _message(parsed[0])
    if isinstance(parsed, dict):
        err = parsed.get("error", parsed)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or "")[:400]
        if isinstance(err, str):
            return err[:400]
        return str(parsed.get("message") or "")[:400]
    return str(parsed or "")[:400]


def _scrub(text: str, secret: str) -> str:
    """Some services repeat the key back in their error messages."""
    text = str(text or "")
    return text.replace(secret, "[your key]") if secret else text


def _seconds(text) -> float | None:
    """'7', '7.66s', '2m59.56s', '250ms' -> seconds."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    try:
        return max(0.0, float(s))
    except ValueError:
        pass
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)", s)
    if not parts:
        return None
    return sum(float(n) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[u] for n, u in parts)


def _wait_from(headers: dict, msg: str) -> float | None:
    """How long a rate-limited service asked us to wait, if it said."""
    if "retry-after-ms" in headers:
        s = _seconds(headers["retry-after-ms"])
        if s is not None:
            return s / 1000.0
    for name in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        s = _seconds(headers.get(name))
        if s is not None:
            return s
    if headers.get("x-ratelimit-reset"):           # a moment in time (OpenRouter)
        try:
            v = float(headers["x-ratelimit-reset"])
            v = v / 1000.0 if v > 1e12 else v
            return max(0.0, v - time.time()) if v > 1e9 else v
        except ValueError:
            pass
    m = re.search(r"(?:try again|retry) in\s+([0-9][0-9.hms ]*)", msg or "", re.I)
    return _seconds(m.group(1)) if m else None


def _human(seconds: float) -> str:
    if seconds < 90:
        return "a minute"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    return f"{round(seconds / 3600)} hours"


def _content(data: dict) -> str:
    try:
        msg = data["choices"][0].get("message") or {}
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
    text = msg.get("content")
    if isinstance(text, list):                    # some services send parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    # Thinking models sometimes write their reasoning into the answer.
    return re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S).strip()


def _call(base: str, secret: str, model: str, messages: list, *, label: str,
          json_mode: bool = False, temperature: float = 0.7,
          timeout: float = 300.0, max_tokens: int = 4096) -> str:
    """One chat request, made to work across companies.

    The OpenAI-style API is a family, not a standard. Some services refuse
    JSON mode, some newer models refuse `max_tokens` and want
    `max_completion_tokens`, reasoning models refuse a temperature. Each of
    those refusals names the setting, so the request is adjusted and sent
    again, and the adjustment is remembered for that service and model.

    Free allowances are counted per minute and per day. A short wait the
    service asks for is waited out; a long one means the day's allowance is
    gone, and that is said in words instead of retried for hours.
    """
    memo = _ADAPT.setdefault((base, model), set())
    waited, attempts = 0.0, 0
    while True:
        attempts += 1
        body = {"model": model, "messages": messages}
        if "no_temp" not in memo:
            body["temperature"] = temperature
        body["max_completion_tokens" if "mct" in memo else "max_tokens"] = max_tokens
        if json_mode and "no_json" not in memo:
            body["response_format"] = {"type": "json_object"}
        try:
            status, headers, data = _request("POST", base + "/chat/completions",
                                             secret, body, timeout)
        except OSError:
            if attempts <= 3:
                time.sleep(2 * attempts)
                continue
            raise WriterUnavailable(f"Could not reach {label}. Check the internet "
                                    "connection, then try again.") from None
        msg = _scrub(_message(data), secret)
        if status == 200 and isinstance(data, dict) and data.get("choices"):
            return _content(data)
        if status == 200:                       # an error dressed as a success
            if attempts <= 2:
                time.sleep(3)
                continue
            raise WriterUnavailable(f"{label} answered with an error: "
                                    f"{msg or 'no text came back'}")
        if status in (401, 403):
            raise WriterUnavailable(f"{label} did not accept the key. Paste it again "
                                    f"in Settings, in full. ({msg or status})")
        if status == 402:
            raise WriterUnavailable(f"{label} says this key has no credit left. "
                                    f"({msg or status})")
        if status == 404:
            raise WriterUnavailable(f'{label} has no model called "{model}", or the '
                                    "address is wrong. Pick the model again in Settings.")
        if status == 429:
            wait = _wait_from(headers, msg)
            daily = wait is None and re.search(r"per.?day|daily|quota", msg, re.I)
            if wait is None and not daily:
                wait = min(60.0, 5.0 * 2 ** min(attempts - 1, 4))
            if daily or wait > 90 or waited + wait > 240:
                when = f" It should work again in about {_human(wait)}." if wait else ""
                raise WriterUnavailable(
                    f"{label} has run out of free requests for now.{when} You can "
                    "switch to writing on this computer in Settings meanwhile.")
            log("info", f"{label} asked Shortsmith to slow down; waiting {wait:.0f}s")
            time.sleep(wait + 0.5)
            waited += wait
            continue
        if status in (400, 422) and attempts <= 6:
            low = msg.lower()
            if (json_mode and "no_json" not in memo and re.search(
                    r"response_format|json_object|json mode|json_validate|"
                    r"failed_generation|json schema", low)):
                memo.add("no_json")
                continue
            if "mct" not in memo and "max_tokens" in low:
                memo.add("mct")
                continue
            if "no_temp" not in memo and "temperature" in low:
                memo.add("no_temp")
                continue
        if status >= 500 and attempts <= 3:
            time.sleep(3 * attempts)
            continue
        raise WriterUnavailable(f"{label} did not take the request ({status}). "
                                f"{msg}".strip())


def chat(system: str, user: str, model_info: dict, *, json_mode: bool = False,
         temperature: float = 0.7, timeout: float = 300.0) -> str:
    """The writing stage's request, sent to the chosen service.

    The address and model come from the saved setting, never from what a
    render remembered: a render resumed after the person switched companies
    would otherwise send the NEW company's key to the OLD company's address.
    """
    cfg = load()
    secret = key(cfg)
    if cfg.get("mode") != "online" or not secret:
        raise WriterUnavailable("Online writing is not set up any more. Choose it "
                                "again in Settings, or write on this computer.")
    return _call(cfg["base_url"], secret, cfg.get("model") or "",
                 [{"role": "system", "content": system},
                  {"role": "user", "content": user}],
                 label=label_of(cfg), json_mode=json_mode, temperature=temperature,
                 timeout=min(float(timeout or 300), 300.0))


def list_models(base: str, secret: str, label: str) -> list | None:
    """The service's writing models, or None if it does not list them."""
    try:
        status, _, data = _request("GET", base + "/models", secret, None, 20)
    except OSError:
        raise WriterUnavailable(f"Could not reach {label}. Check the address and "
                                "the internet connection.") from None
    if status in (401, 403):
        raise WriterUnavailable(f"{label} did not accept the key. Check that you "
                                "copied all of it.")
    if status != 200 or not isinstance(data, dict):
        return None
    rows = data.get("data") if data.get("data") is not None else data.get("models")
    found = set()
    for row in rows or []:
        mid = (row.get("id") or row.get("name")) if isinstance(row, dict) else row
        mid = str(mid or "").strip()
        if mid.startswith("models/"):
            mid = mid[len("models/"):]
        if mid and not any(x in mid.lower() for x in _NOT_WRITERS):
            found.add(mid)
    return sorted(found)[:300]


def _pick(models: list, prefer: list) -> str:
    for want in prefer:
        hits = [m for m in models if want in m.lower()]
        if hits:
            # The plainest name, and the newest version when names tie.
            return min(sorted(hits, reverse=True), key=len)
    return models[0] if models else ""


def _resolve(form: dict) -> tuple:
    pid = str(form.get("provider") or "").strip()
    pre = preset(pid)
    if not pre:
        raise ValueError("Choose a company first.")
    base = clean_base(form.get("base_url")) if pid == "other" else pre["base_url"]
    typed = str(form.get("key") or "").strip()
    if typed:
        if len(typed) > 400 or re.search(r"\s", typed):
            raise ValueError("That does not look like a key. Copy it again: it is "
                             "one unbroken line of letters and numbers.")
        secret = typed
    else:
        cfg = load()
        secret = key(cfg) if cfg.get("base_url") == base else ""
    if not secret:
        who = pre["label"] if pid != "other" else "that service"
        raise ValueError(f"Paste your key from {who} first.")
    model = str(form.get("model") or "").strip()
    if model.startswith("models/"):
        model = model[len("models/"):]
    if len(model) > 200:
        raise ValueError("That model name is too long.")
    return pid, base, secret, model


def check(form: dict) -> dict:
    """Try a key for real: list the models, then ask the chosen one one word."""
    try:
        pid, base, secret, model = _resolve(form)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    pre = preset(pid)
    label = pre["label"] if pid != "other" else (_host(base) or "that service")
    models = None
    try:
        models = list_models(base, secret, label)
        if not model:
            model = _pick(models or [], pre.get("prefer") or [])
        if not model:
            return {"ok": False, "models": [],
                    "error": f"{label} did not list its models. Type the model's "
                             "name in the Model box."}
        _call(base, secret, model,
              [{"role": "user", "content": "Reply with the single word: ok"}],
              label=label, temperature=0, timeout=60, max_tokens=16)
    except WriterUnavailable as exc:
        return {"ok": False, "error": str(exc), "models": models or []}
    _VERIFIED[(base, model, _digest(secret))] = time.time()
    return {"ok": True, "model": model, "models": models or [],
            "message": f"{label} accepted the key, and {model} answered. "
                       "Press Save to use it."}


def save(form: dict) -> dict:
    """Save the choice. Online is saved only once the key has actually worked."""
    mode = str(form.get("mode") or "")
    cfg = load()
    if mode == "local":
        cfg["mode"] = "local"
        _write(cfg)
        return public_state()
    if mode != "online":
        return {"error": "Choose this computer or an online AI."}
    try:
        pid, base, secret, model = _resolve(form)
    except ValueError as exc:
        return {"error": str(exc)}
    seen = _VERIFIED.get((base, model, _digest(secret)), 0)
    if not model or time.time() - seen > 900:
        r = check({**form, "model": model})
        if not r.get("ok"):
            return {"error": r.get("error") or "That key did not work."}
        model = r["model"]
    try:
        sealed = _seal(secret)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not lock the key: {exc}")
        return {"error": "Shortsmith could not store the key safely on this "
                         "computer, so it was not saved."}
    cfg.update(version=1, mode="online", provider=pid, base_url=base, model=model,
               key=sealed, key_hint=secret[-4:])
    _write(cfg)
    log("info", f"writing online with {label_of(cfg)} ({model}), chosen in Settings")
    return public_state()


def local_choice() -> str:
    """The model on this computer the person chose, or "" for automatic."""
    return str(load().get("local_model") or "")


def set_local_choice(tag: str) -> None:
    """Remember the chosen local model. Choosing one means writing locally."""
    cfg = load()
    if tag:
        cfg["local_model"] = tag
        cfg["mode"] = "local"
    else:
        cfg.pop("local_model", None)
    _write(cfg)


def forget() -> dict:
    """Remove the key and go back to writing on this computer."""
    cfg = load()
    cfg.pop("key", None)
    cfg.pop("key_hint", None)
    if cfg.get("mode") == "online":
        cfg["mode"] = "local"
    _write(cfg)
    log("info", "online writing key removed; writing on this computer")
    return public_state()
