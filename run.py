#!/usr/bin/env python
"""StoryForge CLI — run one lane for one channel.

    python run.py --channel history_mysteries --lane night --mock
    python run.py --channel gaming_lore --lane day --topic "the fall of an ancient AI"
    python run.py --list

Flags:
    --channel   channel key (see config/*.yaml)   e.g. history_mysteries | gaming_lore
    --lane      night (local Wan2GP) | day (cloud)
    --mock      zero keys / zero network / zero GPU — still emits a real mp4
    --topic     optional forced topic
    --publish   upload PRIVATE to YouTube if creds exist (never public)
    --run-id    resume a previous run id (checkpointed)
"""
from __future__ import annotations

import argparse
import sys

from storyforge.config import list_channels
from storyforge.graph import run_one


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="StoryForge — one lane, one channel")
    p.add_argument("--channel", help="channel key (config/<key>.yaml)")
    p.add_argument("--lane", choices=["night", "day"], default="night")
    p.add_argument("--mock", action="store_true", help="mock mode: no keys/network/GPU")
    p.add_argument("--topic", default="", help="optional forced topic")
    p.add_argument("--publish", action="store_true", help="upload PRIVATE if creds exist")
    p.add_argument("--voice", default=None, choices=["adam", "squeaky"],
                   help="narrator style: adam (deep) | squeaky (chipmunk)")
    p.add_argument("--run-id", default=None, help="resume an existing run id")
    p.add_argument("--list", action="store_true", help="list channels and exit")
    args = p.parse_args(argv)

    channels = list_channels()
    if args.list:
        print("Channels:")
        for c in channels:
            print(f"  - {c}")
        return 0

    channel = args.channel or (channels[0] if channels else "history_mysteries")
    if channels and channel not in channels:
        print(f"[!] unknown channel '{channel}'. Available: {', '.join(channels)}", file=sys.stderr)
        return 2

    print("=" * 60)
    print(f"StoryForge · channel={channel} · lane={args.lane} · mock={args.mock}")
    print("=" * 60)

    state = run_one(channel, args.lane, mock=args.mock, publish=args.publish,
                    topic=args.topic, run_id=args.run_id, voice=args.voice)

    final = state.get("final_video_path")
    print()
    if final:
        print("✅ DONE")
        print(f"   video : {final}")
        print(f"   report: {state.get('report_path')}")
        print(f"   thumb : {state.get('thumbnail_path')}")
        if state.get("fallbacks"):
            print(f"   fallbacks fired: {len(state['fallbacks'])} (see report / feed)")
        print("\n   Review & approve it in the PWA (pending/ -> approved/).")
        return 0
    if state.get("deferred"):
        print("🌙 QUEUED — night lane submitted clips to Wan2GP; re-run to assemble.")
        return 0
    print("❌ No final video produced — check the bottleneck feed / logs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
