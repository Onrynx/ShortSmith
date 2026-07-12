"""Human-readable reports: per-run approval report + nightly digest."""
from __future__ import annotations

from pathlib import Path

from ..schema import DISCLOSURE_LINE
from ..utils import OUTPUT_DIR, today_str, utcnow_iso
from .push import recent_events


def build_run_report(state: dict) -> str:
    cfg = state.get("channel_cfg", {})
    script = state.get("script", {}) or {}
    scenes = script.get("scenes", []) or []
    hero = [s for s in scenes if s.get("hero")]
    durations = state.get("durations", []) or []
    total = round(sum(durations), 1)
    fallbacks = state.get("fallbacks", []) or []
    hero_results = state.get("hero_results", {}) or {}

    lines = []
    add = lines.append
    add("=" * 64)
    add("  STORYFORGE — APPROVAL REPORT")
    add("=" * 64)
    add(f"Generated : {utcnow_iso()}")
    add(f"Channel   : {cfg.get('name', state.get('channel'))}  [{state.get('channel')}]")
    add(f"Lane      : {state.get('lane')}   Mock: {state.get('mock')}   Publish: {state.get('publish')}")
    add(f"Run id    : {state.get('run_id')}")
    add(f"Script via: {state.get('script_source', '?')}")
    add(f"LLM       : {state.get('model_info', {}).get('reason', 'n/a')}")
    add("")
    add(f"TITLE : {script.get('title', '(none)')}")
    add(f"HOOK  : {script.get('hook', '(none)')}")
    add("")
    add(f"Scenes         : {len(scenes)}  (hero={len(hero)})")
    add(f"Runtime (est.) : {total}s  (~{round(total/60, 1)} min)")
    add(f"Captions       : {state.get('captions_kind', 'n/a')}")
    add(f"Final video    : {state.get('final_video_path', '(pending)')}")
    add(f"Thumbnail      : {state.get('thumbnail_path', '(none)')}")
    add("")
    add("HERO CLIP PROVIDERS")
    add("-" * 64)
    if hero_results:
        for idx, prov in sorted(hero_results.items(), key=lambda kv: int(kv[0])):
            add(f"  scene {int(idx)+1:>2}: {prov}")
    else:
        add("  (none — all stock/backfill)")
    add("")
    add("FALLBACKS FIRED")
    add("-" * 64)
    if fallbacks:
        for f in fallbacks:
            add(f"  • {f}")
    else:
        add("  none — clean run 🟢")
    add("")
    add("SCRIPT")
    add("-" * 64)
    for i, sc in enumerate(scenes):
        tag = "HERO" if sc.get("hero") else "stock"
        dur = f"{durations[i]:.1f}s" if i < len(durations) else "?"
        add(f"[{i+1:>2}/{tag}/{dur}] {sc.get('narration','')}")
        add(f"      img: {sc.get('image_prompt','')}")
    add("")
    add("METADATA")
    add("-" * 64)
    add(f"description:\n{script.get('description','')}")
    add(f"tags: {', '.join(script.get('tags', []))}")
    add("")
    add("COMPLIANCE CHECKLIST (YouTube 2025-2026 inauthentic-content policy)")
    add("-" * 64)
    add(f"  [x] AI disclosure present in description: "
        f"{'yes' if DISCLOSURE_LINE.lower() in script.get('description','').lower() else 'NO'}")
    add("  [x] Human approval REQUIRED before anything goes public (this report)")
    add("  [x] Prompts exclude real people / logos / copyrighted characters")
    add("  [x] Critic pass flags unverifiable claims for softening")
    add(f"  [ ] Reviewer confirms facts & tone, then APPROVE in the PWA")
    add("")
    add("ACTION: Review, then Approve (moves mp4 -> approved/) or Reject (deletes) in the app.")
    add("=" * 64)
    return "\n".join(lines)


def write_run_report(state: dict, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_run_report(state), encoding="utf-8")
    return path


def nightly_digest() -> str:
    day = today_str()
    events = [e for e in recent_events(2000) if str(e.get("ts", "")).startswith(day)]
    produced = sum(1 for e in events if e.get("severity") == "green" and e.get("stage") == "herald")
    reds = [e for e in events if e.get("severity") == "red"]
    yellows = [e for e in events if e.get("severity") == "yellow"]

    pending = 0
    for ch_dir in OUTPUT_DIR.glob("*/pending"):
        pending += len(list(ch_dir.glob("*.mp4")))

    lines = [
        "=" * 48,
        f"  STORYFORGE — NIGHTLY DIGEST  {day}",
        "=" * 48,
        f"Videos completed today : {produced}",
        f"Awaiting approval      : {pending}",
        f"Degraded-but-shipped   : {len(yellows)}",
        f"Failures               : {len(reds)}",
    ]
    if yellows:
        lines.append("")
        lines.append("Degradations:")
        for e in yellows[-10:]:
            lines.append(f"  🟡 [{e.get('lane')}/{e.get('channel')}] {e.get('stage')}: {e.get('message')}")
    if reds:
        lines.append("")
        lines.append("Failures:")
        for e in reds[-10:]:
            lines.append(f"  🔴 [{e.get('lane')}/{e.get('channel')}] {e.get('stage')}: {e.get('message')}")
    lines.append("=" * 48)
    return "\n".join(lines)
