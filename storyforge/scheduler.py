"""Scheduler + daily lane picker.

Each day the two channels are assigned to the two lanes (night=local Wan2GP,
day=cloud) INTERACTIVELY: the scheduler pushes an "Assign lanes for today"
notification, the user taps a choice in the PWA (which writes the assignment via
the app), and the scheduler dispatches. If the user hasn't answered by a cutoff
(default 30 min) it falls through to the previous day's mapping so nothing stalls.

CLI:
  python -m storyforge.scheduler picker                 # push the assign-lanes prompt
  python -m storyforge.scheduler daily   [--mock]       # ensure assignment, run both lanes
  python -m storyforge.scheduler dispatch --lane night  # run one lane for today's channel
  python -m storyforge.scheduler digest                 # push the nightly digest
  python -m storyforge.scheduler set --night <ch> --day <ch>
"""
from __future__ import annotations

import argparse
import time

from .config import list_channels
from .graph import run_one
from .notify import nightly_digest, notify
from .state import PipelineState
from .utils import STATE_DIR, env, log, read_json, today_str, write_json

ASSIGN_FILE = STATE_DIR / "lane_assignment.json"
DEFAULT_CUTOFF_MIN = int(env("LANE_PICK_CUTOFF_MIN", "30") or 30)


# --------------------------------------------------------------------------- #
# Assignment persistence
# --------------------------------------------------------------------------- #
def get_assignment() -> dict:
    return read_json(ASSIGN_FILE, {}) or {}


def set_assignment(night: str, day: str, *, for_today: bool = True) -> dict:
    data = get_assignment()
    data["last"] = {"night": night, "day": day}
    if for_today:
        data["date"] = today_str()
        data["night"] = night
        data["day"] = day
    write_json(ASSIGN_FILE, data)
    log("ok", f"lane assignment set — night:{night}  day:{day}")
    return data


def _default_pair() -> tuple[str, str]:
    chans = list_channels() or ["history_mysteries", "gaming_lore"]
    night = chans[0]
    day = chans[1] if len(chans) > 1 else chans[0]
    return night, day


def assignment_is_for_today() -> bool:
    d = get_assignment()
    return d.get("date") == today_str() and bool(d.get("night")) and bool(d.get("day"))


# --------------------------------------------------------------------------- #
# The interactive picker
# --------------------------------------------------------------------------- #
def push_picker() -> None:
    chans = list_channels() or list(_default_pair())
    notify("yellow", "scheduler", "-", "lane-picker",
           "Assign today's lanes: tap in the app to choose which channel goes "
           "night (local) vs day (cloud).",
           details={"action": "lane_picker", "channels": chans})
    log("info", "lane-picker notification pushed; awaiting tap in the PWA")


def ensure_today_assignment(cutoff_min: int = DEFAULT_CUTOFF_MIN,
                            wait: bool = True) -> dict:
    """Return today's mapping, waiting for the user's tap then falling through."""
    if assignment_is_for_today():
        return get_assignment()

    push_picker()
    if wait:
        deadline = time.time() + cutoff_min * 60
        poll = 15
        log("info", f"waiting up to {cutoff_min} min for lane pick ...")
        while time.time() < deadline:
            if assignment_is_for_today():
                log("ok", "lane pick received from app")
                return get_assignment()
            time.sleep(poll)

    # fall through to yesterday's mapping (or a sane default)
    last = get_assignment().get("last") or {}
    night = last.get("night") or _default_pair()[0]
    day = last.get("day") or _default_pair()[1]
    notify("yellow", "scheduler", "-", "lane-picker",
           f"No pick within cutoff — reusing previous mapping (night:{night}, day:{day})")
    return set_assignment(night, day)


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def dispatch_lane(lane: str, *, mock: bool = False, publish: bool = False,
                  cutoff_min: int = DEFAULT_CUTOFF_MIN, wait: bool = True) -> PipelineState:
    mapping = ensure_today_assignment(cutoff_min, wait=wait)
    channel = mapping.get(lane)
    if not channel:
        raise RuntimeError(f"no channel assigned to lane '{lane}'")
    notify("info", lane, channel, "scheduler", f"dispatching {lane} lane -> {channel}", push=False)
    return run_one(channel, lane, mock=mock, publish=publish)


def run_daily(*, mock: bool = False, publish: bool = False,
              cutoff_min: int = DEFAULT_CUTOFF_MIN, wait: bool = True) -> dict:
    mapping = ensure_today_assignment(cutoff_min, wait=wait)
    results = {}
    for lane in ("night", "day"):
        try:
            st = dispatch_lane(lane, mock=mock, publish=publish, cutoff_min=cutoff_min, wait=False)
            results[lane] = st.get("final_video_path") or "(no output)"
        except Exception as exc:  # noqa: BLE001
            notify("red", lane, mapping.get(lane, "?"), "scheduler", f"{lane} lane failed: {exc}")
            results[lane] = f"FAILED: {exc}"
    notify("green", "scheduler", "-", "daily",
           f"daily cycle complete — night:{results.get('night')}  day:{results.get('day')}")
    return results


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="storyforge.scheduler", description="StoryForge scheduler")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("picker", help="push the assign-lanes notification")

    p_daily = sub.add_parser("daily", help="ensure assignment then run both lanes")
    p_daily.add_argument("--mock", action="store_true")
    p_daily.add_argument("--publish", action="store_true")
    p_daily.add_argument("--no-wait", action="store_true", help="don't wait for the tap")
    p_daily.add_argument("--cutoff", type=int, default=DEFAULT_CUTOFF_MIN)

    p_disp = sub.add_parser("dispatch", help="run a single lane for today's channel")
    p_disp.add_argument("--lane", choices=["night", "day"], required=True)
    p_disp.add_argument("--mock", action="store_true")
    p_disp.add_argument("--publish", action="store_true")
    p_disp.add_argument("--no-wait", action="store_true")
    p_disp.add_argument("--cutoff", type=int, default=DEFAULT_CUTOFF_MIN)

    p_set = sub.add_parser("set", help="set today's lane assignment directly")
    p_set.add_argument("--night", required=True)
    p_set.add_argument("--day", required=True)

    sub.add_parser("digest", help="push the nightly digest")
    sub.add_parser("show", help="print current assignment")

    args = p.parse_args(argv)

    if args.cmd == "picker":
        push_picker()
    elif args.cmd == "daily":
        res = run_daily(mock=args.mock, publish=args.publish,
                        cutoff_min=args.cutoff, wait=not args.no_wait)
        print(res)
    elif args.cmd == "dispatch":
        st = dispatch_lane(args.lane, mock=args.mock, publish=args.publish,
                           cutoff_min=args.cutoff, wait=not args.no_wait)
        print(st.get("final_video_path") or "(no output)")
    elif args.cmd == "set":
        print(set_assignment(args.night, args.day))
    elif args.cmd == "digest":
        text = nightly_digest()
        print(text)
        notify("green", "scheduler", "-", "digest", "Nightly digest ready", details={"text": text})
    elif args.cmd == "show":
        print(get_assignment())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
