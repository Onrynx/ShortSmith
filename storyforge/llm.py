"""LLM backend selection + access. All free, default LOCAL (Ollama).

Priority (non-mock):
  1. Ollama at OLLAMA_BASE_URL if reachable. Model chosen by free RAM:
       >= 16 GB free -> prefer a 14B, else a 3B-class model.
     The desired tag is reconciled against *installed* models so it works out of
     the box (e.g. qwen3:14b requested but qwen2.5:14b installed -> use that).
  2. Gemini (gemini/gemini-2.5-flash) if GEMINI_API_KEY is set.
  3. Mock (canned) — never requires a paid service.

--mock forces the mock backend with zero network calls.
"""
from __future__ import annotations

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
# Odysseus — PewDiePie's self-hosted AI workspace (github.com/pewdiepie-archdaemon
# /odysseus). Runs at localhost:7000 and fronts model backends (Ollama, vLLM,
# llama.cpp, OpenRouter) behind an OpenAI-compatible /v1 API. We treat it as the
# preferred local "brain": own your AI instead of renting it.
# --------------------------------------------------------------------------- #
def odysseus_base_url() -> str:
    return (env("ODYSSEUS_URL", "http://localhost:7000") or "http://localhost:7000").rstrip("/")


def odysseus_api_base() -> str:
    return odysseus_base_url() + (env("ODYSSEUS_API_PATH", "/v1") or "/v1")


def _odysseus_key() -> str:
    return env("ODYSSEUS_API_KEY", "odysseus") or "odysseus"


def _odysseus_models(api_base: str) -> list[str]:
    try:
        data = http_get_json(f"{api_base}/models",
                             headers={"Authorization": f"Bearer {_odysseus_key()}"}, timeout=4)
        items = data.get("data", data) if isinstance(data, dict) else data
        return [m.get("id", "") for m in items] if isinstance(items, list) else []
    except Exception:
        return []


def odysseus_ui_reachable() -> bool:
    """True if the Odysseus web app answers at :7000 (even a login redirect)."""
    return host_reachable(odysseus_base_url(), timeout=3)


def odysseus_reachable() -> bool:
    """True only if Odysseus actually serves an OpenAI-compatible /v1 that we can
    generate against. Odysseus's own API is auth'd under /api and it usually just
    *consumes* model backends, so we require a real /v1/models list before routing
    the crew here — otherwise StoryForge uses the shared Ollama that Odysseus also
    uses (same brain), and never picks a backend that would 404."""
    if env("ODYSSEUS_DISABLE"):
        return False
    return len(_odysseus_models(odysseus_api_base())) > 0


def select_model(mock: bool = False) -> dict:
    """Return {backend, model, base_url, reason}. Never raises."""
    if mock:
        return {"backend": "mock", "model": "mock", "base_url": "", "reason": "--mock: canned generator, zero network"}

    # 1) Odysseus (preferred — self-hosted, OpenAI-compatible gateway)
    if odysseus_reachable():
        api_base = odysseus_api_base()
        model = env("ODYSSEUS_MODEL") or (_odysseus_models(api_base)[:1] or [""])[0] or "default"
        return {"backend": "odysseus", "model": model, "base_url": api_base,
                "reason": f"Odysseus up at {odysseus_base_url()}; using '{model}'"}

    free = detect_free_ram_gb()
    total = detect_total_ram_gb()
    base = ollama_base_url()

    if host_reachable(f"{base}/api/tags", timeout=4):
        installed = _installed_ollama_models(base)
        override = env("OLLAMA_MODEL")
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

    if env("GEMINI_API_KEY"):
        return {"backend": "gemini", "model": env("GEMINI_MODEL", "gemini-2.5-flash"),
                "base_url": "", "reason": "Ollama unreachable; GEMINI_API_KEY set -> gemini-2.5-flash"}

    return {"backend": "mock", "model": "mock", "base_url": "",
            "reason": "Ollama unreachable and no GEMINI_API_KEY -> canned mock generator"}


# --------------------------------------------------------------------------- #
# CrewAI LLM factory (used by the creative crew)
# --------------------------------------------------------------------------- #
def make_crew_llm(model_info: dict):
    """Return a crewai.LLM or None if CrewAI/backends unavailable."""
    if model_info.get("backend") == "mock":
        return None
    try:
        from crewai import LLM  # type: ignore
    except Exception:
        return None
    try:
        if model_info["backend"] == "odysseus":
            # OpenAI-compatible gateway -> litellm openai/ provider + custom base_url
            return LLM(model=f"openai/{model_info['model']}", base_url=model_info["base_url"],
                       api_key=_odysseus_key(), temperature=0.7)
        if model_info["backend"] == "ollama":
            return LLM(model=f"ollama/{model_info['model']}",
                       base_url=model_info["base_url"], temperature=0.7)
        if model_info["backend"] == "gemini":
            return LLM(model=f"gemini/{model_info['model']}", temperature=0.7)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not build CrewAI LLM: {exc}")
    return None


# --------------------------------------------------------------------------- #
# Direct completion (single-shot fallback path, no CrewAI needed)
# --------------------------------------------------------------------------- #
def complete(system: str, user: str, model_info: dict,
             json_mode: bool = False, temperature: float = 0.7,
             timeout: float = 180.0) -> str:
    """Chat-complete against the selected backend. Raises on failure."""
    backend = model_info.get("backend")
    if backend == "odysseus":
        headers = {"Authorization": f"Bearer {_odysseus_key()}"}
        payload = {
            "model": model_info["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = http_post_json(f"{model_info['base_url']}/chat/completions", payload,
                              headers=headers, timeout=timeout)
        if isinstance(data, dict):
            try:
                return data["choices"][0]["message"]["content"] or ""
            except Exception:
                return str(data)
        return str(data)
    if backend == "ollama":
        payload = {
            "model": model_info["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"
        data = http_post_json(f"{model_info['base_url']}/api/chat", payload, timeout=timeout)
        if isinstance(data, dict):
            return (data.get("message", {}) or {}).get("content", "") or ""
        return str(data)
    if backend == "gemini":
        key = env("GEMINI_API_KEY")
        model = model_info["model"]
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        prompt = f"{system}\n\n{user}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature,
                                 **({"responseMimeType": "application/json"} if json_mode else {})},
        }
        data = http_post_json(url, body, timeout=timeout)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:
            raise RuntimeError(f"unexpected Gemini response: {exc}: {str(data)[:300]}")
    raise RuntimeError(f"backend '{backend}' cannot complete()")
