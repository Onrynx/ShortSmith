"""Which model to run, and how to drive it.

Two jobs:

1. RECOMMEND. Given a hardware scan, pick the best writer this computer can
   actually run, and describe the alternatives honestly — including what gets
   worse as they get smaller. That honesty is the point: a 1.5B model does not
   write the same story as a 14B one, and the setup screen should say so rather
   than let someone discover it after a 10-minute render.

2. TUNE. The generation code has roughly six numbers that decide how hard it
   leans on the model — how many story beats to write per call, how many scenes
   to revise at once, how many passes to spend hitting the target length, and
   the context/output limits. Those were tuned for qwen2.5:14b and hardcoded.
   They are wrong for a 3B model: asking a small model for four beats at once
   produces mush, so it needs smaller asks and more passes to reach the same
   place.

IMPORTANT: the `large` tier reproduces the previously hardcoded values exactly
(beats 4, revisions 4, 3 fit rounds, 16384 ctx, 2048 predict). Swapping a
constant for a lookup must not change what a 14B model produces — there is a
test for this at the bottom of the file.
"""
from __future__ import annotations

import re

from .hardware import scan, usable_gb

# --------------------------------------------------------------------------- #
# Tiers — how hard the writer can be pushed
# --------------------------------------------------------------------------- #
# beats_per_call   story beats written in one request
# revision_batch   scenes rewritten per length-fix request
# fit_rounds       attempts allowed to land on the target duration
# num_ctx          context window
# num_predict      max tokens per reply
# timeout          seconds per request (small models are slower per token here
#                  because they need more, shorter calls)
# scene_scale      multiplier on the channel's scene count
# plan_detail      "full" beat sheet, or a simplified one a small model can hold
TIERS = {
    "xl": {                      # 30B+
        "beats_per_call": 6, "revision_batch": 6, "fit_rounds": 2,
        "num_ctx": 32768, "num_predict": 3072, "timeout": 600,
        "scene_scale": 1.0, "plan_detail": "full",
    },
    "large": {                   # 13-15B  — EXACTLY the old hardcoded values
        "beats_per_call": 4, "revision_batch": 4, "fit_rounds": 3,
        "num_ctx": 16384, "num_predict": 2048, "timeout": 600,
        "scene_scale": 1.0, "plan_detail": "full",
    },
    "medium": {                  # 7-9B
        "beats_per_call": 3, "revision_batch": 3, "fit_rounds": 3,
        "num_ctx": 12288, "num_predict": 1792, "timeout": 600,
        "scene_scale": 0.85, "plan_detail": "full",
    },
    "small": {                   # 3-4B
        "beats_per_call": 2, "revision_batch": 2, "fit_rounds": 4,
        "num_ctx": 8192, "num_predict": 1280, "timeout": 720,
        "scene_scale": 0.65, "plan_detail": "simple",
    },
    "tiny": {                    # <= 2B
        "beats_per_call": 2, "revision_batch": 2, "fit_rounds": 5,
        "num_ctx": 4096, "num_predict": 896, "timeout": 900,
        "scene_scale": 0.5, "plan_detail": "simple",
    },
}


def tier_for_params(billions: float) -> str:
    if billions >= 24:
        return "xl"
    if billions >= 11:
        return "large"
    if billions >= 6:
        return "medium"
    if billions >= 2.5:
        return "small"
    return "tiny"


# --------------------------------------------------------------------------- #
# The catalog shown in the setup menu
# --------------------------------------------------------------------------- #
# download_gb marked (measured) were read off a real install on 2026-10-16;
# the rest are the published sizes and may drift slightly.
CATALOG = [
    {
        "tag": "qwen2.5:32b", "label": "Qwen 2.5 32B", "params": 32,
        "download_gb": 19.0, "min_ram_gb": 24.0,
        "blurb": "Strongest writing. Only worth it on a workstation.",
    },
    {
        "tag": "qwen2.5:14b", "label": "Qwen 2.5 14B", "params": 14,
        "download_gb": 8.4, "min_ram_gb": 9.0, "measured": True,
        "blurb": "Best stories — richest plots, most reliable length and pacing.",
    },
    {
        "tag": "qwen2.5:7b", "label": "Qwen 2.5 7B", "params": 7,
        "download_gb": 4.7, "min_ram_gb": 5.5,
        "blurb": "Good. Slightly flatter plots, needs more rewrite passes.",
    },
    {
        "tag": "qwen3:8b", "label": "Qwen 3 8B", "params": 8,
        "download_gb": 4.9, "min_ram_gb": 5.5, "measured": True,
        "blurb": "Good all-rounder, a little more literal than Qwen 2.5.",
    },
    {
        "tag": "llama3.2:3b", "label": "Llama 3.2 3B", "params": 3,
        "download_gb": 2.0, "min_ram_gb": 3.0,
        "blurb": "Light. Weaker continuity, shorter scenes, simpler structures.",
    },
    {
        "tag": "qwen2.5:3b", "label": "Qwen 2.5 3B", "params": 3,
        "download_gb": 1.9, "min_ram_gb": 3.0,
        "blurb": "Light, slightly better at following instructions than Llama 3B.",
    },
    {
        "tag": "qwen2.5:1.5b", "label": "Qwen 2.5 1.5B", "params": 1.5,
        "download_gb": 1.0, "min_ram_gb": 2.0,
        "blurb": "Minimum. Basic stories, falls back often. Old laptops only.",
    },
]

# Everything installed besides the writer model: Ollama, ffmpeg, the Kokoro
# voice, the caption model and the app's own runtime. Measured, not guessed.
BASE_INSTALL_GB = 1.9


def params_of(tag: str) -> float:
    """Pull the parameter count out of an Ollama tag ('qwen2.5:14b' -> 14.0).

    Users can type any tag they like, so this has to cope with models that
    aren't in the catalog.
    """
    for entry in CATALOG:
        if entry["tag"] == tag:
            return float(entry["params"])
    m = re.search(r"[:\-]\s*(\d+(?:\.\d+)?)\s*b\b", str(tag), re.I)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 7.0        # unknown model: assume mid-range rather than extremes


def machine_speed(hw: dict | None = None) -> float:
    """Rough throughput score for this computer, 0.2 (slow) to 2.0 (fast).

    Not a benchmark — it is the ratio between what the machine has and what the
    model needs. A 14B model on 6 GB of VRAM spills to the CPU and every call
    costs 30-120 seconds; the same model on 24 GB does not. Quality passes that
    are worth it on one machine are not on the other, so the pipeline asks this
    instead of carrying a fixed trade-off.
    """
    hw = hw or scan()
    vram = float(hw.get("vram_gb") or 0)
    ram = float(hw.get("ram_total_gb") or 8)
    cores = int(hw.get("cpu_cores") or 4)
    if hw.get("unified_memory"):          # Apple Silicon: one fast shared pool
        score = ram / 16.0
    elif vram:
        score = vram / 8.0 + cores / 32.0
    else:                                 # CPU-only: slow whatever the RAM
        score = min(0.6, cores / 24.0)
    return round(max(0.2, min(2.0, score)), 2)


def profile_for(tag: str, hw: dict | None = None) -> dict:
    """The tuning knobs for a model on THIS machine.

    Model size sets the baseline; the machine decides how much optional quality
    work is affordable on top. `quality_passes` is what the pipeline consults
    before spending a call on anything that is not strictly required.
    """
    tier = tier_for_params(params_of(tag))
    prof = {"tier": tier, "model": tag, **TIERS[tier]}
    speed = machine_speed(hw)
    prof["machine_speed"] = speed
    # A slow machine still gets every correctness pass; it only skips the
    # optional ones unless something was actually detected.
    prof["quality_passes"] = "full" if speed >= 0.95 else (
        "balanced" if speed >= 0.55 else "lean")
    prof["always_continuity"] = speed >= 0.95
    if speed < 0.55:                       # give a slow box more wall-clock room
        prof["timeout"] = int(prof["timeout"] * 1.5)
    return prof


def catalog_for(hw: dict | None = None) -> list[dict]:
    """The menu: every model, with whether this machine can run it and why not."""
    hw = hw or scan()
    budget = usable_gb(hw)
    disk = float(hw.get("disk_free_gb") or 0)
    out = []
    for e in CATALOG:
        need_disk = e["download_gb"] + BASE_INSTALL_GB
        fits_mem = e["min_ram_gb"] <= budget
        fits_disk = need_disk <= disk if disk else True
        reason = ""
        if not fits_mem:
            reason = f"needs about {e['min_ram_gb']:.0f} GB of usable memory"
        elif not fits_disk:
            reason = f"needs {need_disk:.1f} GB free disk"
        out.append({
            **e,
            "tier": tier_for_params(e["params"]),
            "total_install_gb": round(need_disk, 1),
            "runnable": bool(fits_mem and fits_disk),
            "reason": reason,
        })
    return out


def recommend(hw: dict | None = None) -> dict:
    """Best runnable model for this machine, with the menu alongside it."""
    hw = hw or scan()
    options = catalog_for(hw)
    runnable = [o for o in options if o["runnable"]]
    # biggest that fits; falls back to the smallest if nothing does
    best = max(runnable, key=lambda o: o["params"]) if runnable else options[-1]
    return {
        "hardware": hw,
        "usable_gb": usable_gb(hw),
        "recommended": best["tag"],
        "profile": profile_for(best["tag"]),
        "options": options,
    }


def explain(tag: str) -> str:
    """One line on what choosing this model means for the output."""
    p = profile_for(tag)
    t = p["tier"]
    if t in ("xl", "large"):
        return "Full-length stories, written in large passes, with few rewrites."
    if t == "medium":
        return ("Slightly shorter stories, written in smaller passes — the same "
                "shape, a little less texture.")
    if t == "small":
        return ("Fewer scenes and simpler structure. Written two beats at a time "
                "with extra passes to reach the length you asked for.")
    return ("Short, simple stories. Written in small pieces with several "
            "correction passes; expect the canned fallback sometimes.")


if __name__ == "__main__":       # python -m shortsmith.model_profiles
    import json

    # Guard: the large tier must still reproduce the old hardcoded constants.
    old = {"beats_per_call": 4, "revision_batch": 4, "fit_rounds": 3,
           "num_ctx": 16384, "num_predict": 2048}
    cur = profile_for("qwen2.5:14b")
    drift = {k: (v, cur[k]) for k, v in old.items() if cur[k] != v}
    print("qwen2.5:14b unchanged from the old hardcoded values:",
          "YES" if not drift else f"NO -> {drift}")

    rec = recommend()
    print("\n" + "-" * 62)
    print("recommended:", rec["recommended"], "| usable", rec["usable_gb"], "GB")
    print("-" * 62)
    for o in rec["options"]:
        mark = "  " if o["runnable"] else "x "
        star = " <= recommended" if o["tag"] == rec["recommended"] else ""
        print(f"{mark}{o['tag']:16} {o['download_gb']:5.1f} GB dl  "
              f"{o['total_install_gb']:5.1f} GB total  [{o['tier']:6}]"
              f"{'  ' + o['reason'] if o['reason'] else ''}{star}")
    print("\nprofile:", json.dumps(rec["profile"]))
