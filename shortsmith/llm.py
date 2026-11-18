"""LLM backend selection + access. Local only — there is nowhere else to go.

Priority (non-mock):
  1. Ollama at OLLAMA_BASE_URL if reachable. Model chosen by free RAM:
       >= 16 GB free -> prefer a 14B, else a 3B-class model.
     The desired tag is reconciled against *installed* models so it works out of
     the box (e.g. qwen3:14b requested but qwen2.5:14b installed -> use that).
  2. Mock (canned), with a message saying Ollama is not answering.

There is deliberately no hosted fallback. An earlier version dropped through to
Gemini whenever Ollama was down and an API key happened to be present, which
meant the brief and every draft went to Google because of a line in a config
file — while the app told the user nothing leaves this computer.

Online writing (writer.py) is not a fallback either. It is used only when
the person chose it in Settings and gave their own key, and a failure there
stops the render and says why rather than quietly writing somewhere else.

--mock forces the mock backend with zero network calls.
"""
from __future__ import annotations

import time
from typing import Optional

from .config import detect_free_ram_gb, detect_total_ram_gb
from .utils import env, host_reachable, http_get_json, http_post_json, log

# Ranked preferences. First installed match wins.
BIG_PREFS = ["qwen3:14b", "qwen2.5:14b", "qwen3:8b", "llama3.1:8b", "llama3:latest"]
SMALL_PREFS = ["llama3.2:3b", "qwen3:4b", "qwen2.5:3b", "phi3:mini", "qwen3:8b", "llama3:latest"]


def ollama_base_url() -> str:
    return env("OLLAMA_BASE_URL", "http://localhost:11434") or "http://localhost:11434"


def _installed_ollama_models(base: str) -> list[str]:
    try:
        data = http_get_json(f"{base}/api/tags", timeout=4)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
def select_model(mock: bool = False) -> dict:
    """Return {backend, model, base_url, reason}. Never raises."""
    if mock:
        return {"backend": "mock", "model": "mock", "base_url": "", "reason": "--mock: canned generator, zero network"}

    # Online, when the person chose it and gave a key that works. A choice
    # that is missing its key falls through to this computer, which is the
    # more private of the two, not to anywhere else.
    from . import writer
    if writer.ready():
        w = writer.load()
        return {"backend": "openai", "model": w["model"], "base_url": w["base_url"],
                "reason": f"writing online with {writer.label_of(w)} ({w['model']}), "
                          "as chosen in Settings"}

    free = detect_free_ram_gb()
    total = detect_total_ram_gb()
    base = ollama_base_url()

    if host_reachable(f"{base}/api/tags", timeout=4):
        installed = _installed_ollama_models(base)
        override = env("OLLAMA_MODEL")
        # The model the person chose in Settings, when it is still installed,
        # wins over everything else. There used to be no way to choose: an
        # OLLAMA_MODEL line in a .env file pinned one model no matter what was
        # downloaded, and without that line free memory decided alone. The
        # .env line now applies only when nothing has been chosen.
        chosen = writer.local_choice()
        if chosen and chosen in installed:
            return {"backend": "ollama", "model": chosen, "base_url": base,
                    "reason": f"Ollama up; using {chosen}, chosen in Settings"}
        prefs = ([override] if override else []) + (BIG_PREFS if free >= 16 else SMALL_PREFS)
        size_note = f"{free}GB free / {total}GB total -> {'14B-class' if free >= 16 else '3B/8B-class'}"
        # first preferred that is actually installed
        for want in prefs:
            for have in installed:
                if have == want or have.split(":")[0] == want.split(":")[0]:
                    return {"backend": "ollama", "model": have, "base_url": base,
                            "reason": f"Ollama up ({size_note}); using {have}"}
        # nothing matched but server is up: try the first preference anyway (may pull)
        if installed:
            return {"backend": "ollama", "model": installed[0], "base_url": base,
                    "reason": f"Ollama up ({size_note}); no preferred tag installed, using {installed[0]}"}
        want = prefs[0] if prefs else "llama3.2:3b"
        return {"backend": "ollama", "model": want, "base_url": base,
                "reason": f"Ollama up ({size_note}); no models installed, will try {want}"}

    # There is no hosted fallback. A cloud model would mean the brief, the plan
    # and every draft leaving this machine the moment Ollama happened to be
    # down — a failure mode nobody asked for, triggered by an API key sitting
    # in a config file. If the local engine is not answering, say so.
    return {"backend": "mock", "model": "mock", "base_url": "",
            "reason": "Ollama is not reachable — start it, or open Settings to "
                      "install a model"}


def unload(model_info: dict) -> None:
    """Ask Ollama to drop the writer from memory now.

    It otherwise stays loaded for five minutes after its last request, which
    is exactly while the voice, the captions and the video encoder all want
    memory of their own. On an 8 GB laptop that is the difference between
    working and swapping.
    """
    if (model_info or {}).get("backend") != "ollama":
        return
    try:
        http_post_json(f"{model_info['base_url']}/api/generate",
                       {"model": model_info["model"], "keep_alive": 0}, timeout=20)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Direct completion (single-shot fallback path, no CrewAI needed)
# --------------------------------------------------------------------------- #
def complete(system: str, user: str, model_info: dict,
             json_mode: bool = False, temperature: float = 0.7,
             timeout: float = 180.0, schema: dict | None = None) -> str:
    """Chat-complete against the selected backend. Raises on failure.

    `schema` is a JSON Schema the reply must fit. Ollama compiles it into a
    grammar and the model cannot write anything that breaks it -- so "exactly
    four scenes" stops being a request and becomes a fact. Small models
    ignore counts written in words all the time; asked in words for one scene
    and given a four-scene shape, an 8B wrote four.
    """
    backend = model_info.get("backend")
    # Context/output limits are per-model, not global — see model_profiles.
    from .model_profiles import profile_for
    _prof = profile_for(model_info.get("model", ""))
    if backend == "openai":
        from .writer import chat
        return chat(system, user, model_info, json_mode=json_mode or bool(schema),
                    temperature=temperature, timeout=timeout)
    if backend == "ollama":
        payload = {
            "model": model_info["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            # Ollama defaults to a 4096 context — far too small once the story
            # doctrine + the draft being continued are in the prompt, which
            # silently truncates and produces stubby scripts. The right size
            # depends on the model: a 14B can hold 16k comfortably, a 1.5B
            # cannot, so these come from the model's profile. An explicit env
            # var still wins, for anyone who wants to override by hand.
            "options": {"temperature": temperature,
                        "num_ctx": int(env("OLLAMA_NUM_CTX", "") or _prof["num_ctx"]),
                        "num_predict": int(env("OLLAMA_NUM_PREDICT", "") or _prof["num_predict"])},
        }
        if schema:
            payload["format"] = schema
        elif json_mode:
            payload["format"] = "json"
        if model_info["model"].startswith(("qwen3", "deepseek-r1")):
            payload["think"] = False  # thinking mode is 10x slower; not needed here
        url = f"{model_info['base_url']}/api/chat"
        began = time.time()
        try:
            data = http_post_json(url, payload, timeout=timeout)
        except Exception:
            # An Ollama too old to take a shape refuses at once. Anything that
            # took longer than that failed for another reason, and asking
            # again would only double the wait.
            if not schema or time.time() - began > 20:
                raise
            payload["format"] = "json"
            data = http_post_json(url, payload, timeout=timeout)
        if isinstance(data, dict):
            return (data.get("message", {}) or {}).get("content", "") or ""
        return str(data)
    raise RuntimeError(f"backend '{backend}' cannot complete()")
