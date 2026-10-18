#!/usr/bin/env python
"""Shortsmith CLI — render one short for one channel.

    python run.py --channel gossip_stories --mock
    python run.py --channel gaming_lore --topic "the fall of an ancient AI"
    python run.py --list

Flags:
    --channel   channel key (see config/*.yaml)   e.g. gossip_stories | gaming_lore
    --mock      zero keys / zero network / zero GPU — still emits a real mp4
    --topic     optional forced topic / story starter
    --publish   upload PRIVATE to YouTube if creds exist (never public)
    --run-id    resume a previous run id (checkpointed)
    --lane      internal status slot (default "render"); you never need this
"""
from __future__ import annotations

import argparse
import sys

from shortsmith.config import (default_channel, list_channels,
                               load_channel, render_budget)
from shortsmith.graph import run_one


def _dry_run(channel: str, args) -> int:
    """Plan + write the story and print it, without touching audio or video.

    Renders take 8-15 minutes, which makes judging story quality painfully slow.
    This runs only the parts that decide whether a video is any good.
    """
    from shortsmith.crews.story_crew import generate_script
    from shortsmith.llm import select_model
    from shortsmith.timing import estimate_seconds

    cfg = load_channel(channel)
    model_info = select_model(args.mock)
    print(f"brain: {model_info['backend']}/{model_info['model']}\n")
    script, plan, t = generate_script(
        cfg, args.topic, render_budget(cfg), model_info,
        requested_seconds=args.seconds,
        voice_style=args.voice or "adam",
    )
    est = estimate_seconds(script["scenes"], t["wpm"], t["calibration"])
    words = sum(len(s["narration"].split()) for s in script["scenes"])
    off = 100 * (est - t["target_seconds"]) / max(t["target_seconds"], 1)

    print(f"\nSTRUCTURE : {plan.get('structure')} — {plan.get('why_structure','')}")
    print(f"LOGLINE   : {plan.get('logline')}")
    print(f"SETTING   : {plan.get('setting')}")
    cast = ", ".join(f"{c.get('name')} ({c.get('role')})"
                     for c in (plan.get("cast") or []) if isinstance(c, dict))
    print(f"CAST      : {cast or '(none listed)'}")
    if plan.get("theme"):
        print(f"THEME     : {plan.get('theme')}")
    print(f"TITLE     : {script.get('title')}")
    print(f"LENGTH    : {words} words ≈ {est:.0f}s vs target "
          f"{t['target_seconds']:.0f}s ({off:+.0f}%)\n")
    print("-" * 60)
    for i, s in enumerate(script["scenes"], 1):
        purpose = s.get("purpose", "?")
        print(f"[{i:2}] ({purpose})\n     {s['narration']}")
    print("-" * 60)
    print("\n(dry run — nothing was rendered)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Shortsmith — render one short for one channel")
    p.add_argument("--channel", help="channel key (config/<key>.yaml); defaults to gossip_stories")
    p.add_argument("--lane", default="render", help=argparse.SUPPRESS)  # internal status slot
    p.add_argument("--mock", action="store_true", help="mock mode: no keys/network/GPU")
    p.add_argument("--topic", default="", help="optional forced topic")
    p.add_argument("--publish", action="store_true", help="upload PRIVATE if creds exist")
    p.add_argument("--voice", default=None, choices=["adam", "squeaky"],
                   help="narrator style: adam (deep) | squeaky (chipmunk)")
    p.add_argument("--seconds", type=float, default=None,
                   help="target length in seconds (e.g. 45); the AI plans and "
                        "revises the story to actually fit it")
    p.add_argument("--run-id", default=None, help="resume an existing run id")
    p.add_argument("--list", action="store_true", help="list channels and exit")
    p.add_argument("--dry-run", action="store_true",
                   help="plan + write the story and print it, WITHOUT rendering "
                        "audio or video (seconds, not minutes — use this to test "
                        "story quality and pacing)")
    args = p.parse_args(argv)

    channels = list_channels()
    if args.list:
        print("Channels:")
        for c in channels:
            print(f"  - {c}")
        return 0

    channel = args.channel or default_channel()
    if channels and channel not in channels:
        print(f"[!] unknown channel '{channel}'. Available: {', '.join(channels)}", file=sys.stderr)
        return 2

    print("=" * 60)
    print(f"Shortsmith · channel={channel} · mock={args.mock}")
    print("=" * 60)

    if args.dry_run:
        return _dry_run(channel, args)

    state = run_one(channel, args.lane, mock=args.mock, publish=args.publish,
                    topic=args.topic, run_id=args.run_id, voice=args.voice,
                    seconds=args.seconds)

    final = state.get("final_video_path")
    print()
    if final:
        print("✅ DONE")
        print(f"   video : {final}")
        print(f"   report: {state.get('report_path')}")
        print(f"   thumb : {state.get('thumbnail_path')}")
        if state.get("fallbacks"):
            print(f"   fallbacks fired: {len(state['fallbacks'])} (see report / feed)")
        print("\n   Review & approve it in Discord (pending/ -> approved/).")
        return 0
    if state.get("deferred"):
        print("⏳ QUEUED — clips submitted to Wan2GP; re-run to assemble.")
        return 0
    print("❌ No final video produced — check the bottleneck feed / logs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
