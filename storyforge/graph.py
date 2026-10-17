"""Orchestration — the checkpointed assembly line.

Primary engine: LangGraph StateGraph (script -> voice -> canvas -> cutter ->
herald) with a SqliteSaver checkpointer, so a crash mid-render resumes without
re-running the expensive script/clip stages.

Fallback engine: a stdlib "lite" runner with an equivalent JSON checkpoint, used
when LangGraph isn't installed OR if the LangGraph run raises. Either way the same
five node functions run and the same state is produced — this is what guarantees
MOCK mode completes on a bare machine while honoring the LangGraph design when the
dependency is present.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import load_channel
from .llm import select_model
from .nodes import node_canvas, node_cutter, node_herald, node_script, node_voice
from .notify import notify
from .providers.wan2gp import DeferredRender
from .state import PipelineState, new_run_state
from .utils import STATE_DIR, env, log, read_json, write_json, write_status

STAGES = [
    ("script", node_script),
    ("voice", node_voice),
    ("canvas", node_canvas),
    ("cutter", node_cutter),
    ("herald", node_herald),
]
_ACCUMULATORS = ("fallbacks", "errors")


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# LangGraph engine
# --------------------------------------------------------------------------- #
def _wrap(node_fn):
    """Adapt a node so LangGraph propagates our in-place accumulators too."""
    def inner(state):
        update = node_fn(state) or {}
        update = dict(update)
        for k in _ACCUMULATORS:
            if k in state:
                update[k] = state[k]
        return update
    inner.__name__ = getattr(node_fn, "__name__", "node")
    return inner


def _build_checkpointer():
    """Best-effort SqliteSaver; degrade to MemorySaver; None if unavailable."""
    path = STATE_DIR / "checkpoints.sqlite"
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
        import sqlite3
        conn = sqlite3.connect(str(path), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"SqliteSaver unavailable ({exc}); trying MemorySaver")
    try:
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore
        return MemorySaver()
    except Exception:
        return None


def build_langgraph():
    from langgraph.graph import END, START, StateGraph  # type: ignore

    g = StateGraph(PipelineState)
    for name, fn in STAGES:
        g.add_node(name, _wrap(fn))
    g.add_edge(START, "script")
    for (a, _), (b, _) in zip(STAGES, STAGES[1:]):
        g.add_edge(a, b)
    g.add_edge("cutter", "herald")
    g.add_edge("herald", END)
    return g.compile(checkpointer=_build_checkpointer())


def run_with_langgraph(state: PipelineState) -> PipelineState:
    graph = build_langgraph()
    config = {"configurable": {"thread_id": state["run_id"]}, "recursion_limit": 50}
    # resume if a checkpoint for this thread already has pending work
    try:
        snap = graph.get_state(config)
        resume = bool(getattr(snap, "next", None))
    except Exception:
        resume = False
    if resume:
        log("info", f"LangGraph resuming run {state['run_id']}")
        result = graph.invoke(None, config)
    else:
        result = graph.invoke(state, config)
    return dict(result)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Lite engine (stdlib, JSON checkpoint)
# --------------------------------------------------------------------------- #
def _ckpt_path(state) -> Path:
    return Path(state["workdir"]) / "checkpoint.json"


def run_lite(state: PipelineState, resume: bool = True) -> PipelineState:
    ckpt = _ckpt_path(state)
    completed: set[str] = set()
    if resume and ckpt.exists():
        data = read_json(ckpt, {}) or {}
        saved = data.get("state") or {}
        saved.update({k: v for k, v in state.items() if k in ("mock", "publish", "topic")})
        state = saved  # type: ignore[assignment]
        completed = set(data.get("completed", []))
        if completed:
            log("info", f"lite engine resuming run {state['run_id']} "
                        f"(done: {', '.join(sorted(completed))})")

    for name, fn in STAGES:
        if name in completed:
            log("info", f"skip {name} (checkpointed)")
            continue
        update = fn(state) or {}
        state.update(update)
        completed.add(name)
        try:
            write_json(ckpt, {"completed": sorted(completed), "state": state})
        except Exception as exc:  # noqa: BLE001
            log("warn", f"could not write checkpoint: {exc}")
    return state


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def run_pipeline(state: PipelineState) -> PipelineState:
    lane = state.get("lane", "?")
    channel = state.get("channel", "?")
    engine = (env("STORYFORGE_ENGINE", "auto") or "auto").lower()
    use_lg = engine != "lite" and langgraph_available()

    notify("info", lane, channel, "pipeline",
           f"starting {'MOCK ' if state.get('mock') else ''}run {state['run_id']} "
           f"(engine={'langgraph' if use_lg else 'lite'})", push=False)
    try:
        if use_lg:
            try:
                state = run_with_langgraph(state)
            except DeferredRender:
                raise
            except Exception as exc:  # noqa: BLE001
                notify("yellow", lane, channel, "pipeline",
                       f"LangGraph engine error ({exc}); falling back to lite runner")
                state = run_lite(state)
        else:
            state = run_lite(state)
    except DeferredRender as exc:
        state["deferred"] = True  # type: ignore[typeddict-unknown-key]
        write_status(lane, {"stage": "canvas", "message": str(exc), "deferred": True,
                            "run_id": state.get("run_id"), "channel": channel, "lane": lane})
        notify("yellow", lane, channel, "canvas",
               f"render queued to Wan2GP; re-run to assemble ({exc})")
        return state
    except Exception as exc:  # noqa: BLE001
        notify("red", lane, channel, "pipeline",
               f"RENDER FAILED: {exc}", details={"run_id": state.get("run_id")})
        state.setdefault("errors", []).append(str(exc))
        write_status(lane, {"stage": "failed", "message": str(exc),
                            "run_id": state.get("run_id"), "channel": channel, "lane": lane})
        return state

    if not state.get("final_video_path"):
        notify("red", lane, channel, "pipeline",
               "render produced ZERO output (no final video)")
    else:
        cleanup_workdir(state)
    return state


def cleanup_workdir(state: PipelineState, keep: bool = False) -> int:
    """Delete a finished run's scratch directory. Returns bytes reclaimed.

    Every render writes its intermediates — one cut of the background per
    scene, one segment per scene, a wav per scene — into output/<channel>/
    _work/<run_id>/. Nothing ever removed them, so an audit found 7.76 GB of
    scratch against 0.52 GB of actual videos: 85% of the output folder was
    rubbish, growing by ~300 MB per video.

    Only successful runs are cleaned. A failed run's scratch is the only
    evidence of what went wrong, so it is kept deliberately.
    """
    wd = state.get("workdir")
    if not wd or keep or env("STORYFORGE_KEEP_WORK", "").strip():
        return 0
    path = Path(wd)
    if path.name != state.get("run_id") or "_work" not in path.parts:
        return 0              # refuse to delete anything that isn't scratch
    try:
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except Exception:  # noqa: BLE001
        size = 0
    try:
        shutil.rmtree(path, ignore_errors=True)
        log("info", f"cleaned {size / 1024 ** 2:.0f} MB of working files")
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not clean working files: {exc}")
        return 0
    return size


def run_one(channel: str, lane: str, *, mock: bool = False, publish: bool = False,
            topic: str = "", run_id: str | None = None,
            voice: str | None = None,
            seconds: float | None = None) -> PipelineState:
    """Build state for one render and run the pipeline. Used by run.py + control."""
    cfg = load_channel(channel)
    model_info = select_model(mock)
    state = new_run_state(channel, cfg, lane, mock=mock, publish=publish,
                          topic=topic, run_id=run_id)
    state["model_info"] = model_info
    if voice:
        state["voice_style"] = voice
    if seconds:
        # An explicit length request overrides the channel's configured window;
        # the planner budgets its beats against this number.
        state["requested_seconds"] = float(seconds)
    return run_pipeline(state)
