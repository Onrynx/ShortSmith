"""Channel config loading + hardware detection.

Configs live in config/*.yaml. PyYAML is used when installed; otherwise a small
built-in parser handles the (simple, one-level-nested) config files we ship, so
MOCK mode needs no pip installs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import CONFIG_DIR, log


# --------------------------------------------------------------------------- #
# Minimal YAML (fallback when PyYAML is absent)
# --------------------------------------------------------------------------- #
def _coerce_scalar(v: str) -> Any:
    v = v.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~", ""):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _coerce(v: str) -> Any:
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_coerce_scalar(x) for x in inner.split(",")]
    return _coerce_scalar(v)


def _mini_yaml(text: str) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        if ":" not in content:
            continue  # we don't use block-sequence syntax in our configs
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        key, _, val = content.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _coerce(val)
    return root


def _load_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _mini_yaml(text)


# --------------------------------------------------------------------------- #
# Channel configs
# --------------------------------------------------------------------------- #
_DEFAULTS = {
    "niche": "curious stories",
    "angle": "one gripping story told cinematically",
    "voice": "bm_george",
    "target_minutes": 6,
    "style": "cinematic, moody, atmospheric",
    "banned": "gore, real people, copyrighted characters",
    # One render slot. There are no night/day lanes any more.
    "render_budget": [8, 15],
}


# Gossip-style real-life drama is the default register. Fantasy / game-lore
# channels exist but are never chosen for you — the user has to ask for them.
DEFAULT_CHANNEL = "gossip_stories"


def default_channel() -> str:
    """The channel used when none was named. Never alphabetical chance."""
    chans = list_channels()
    if DEFAULT_CHANNEL in chans:
        return DEFAULT_CHANNEL
    return chans[0] if chans else DEFAULT_CHANNEL


def list_channels() -> list[str]:
    if not CONFIG_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIG_DIR.glob("*.yaml"))


def load_channel(name: str) -> dict:
    """Load a channel config by key (filename stem). Missing keys are defaulted."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        log("warn", f"channel config {path} not found; using defaults")
        cfg = dict(_DEFAULTS)
    else:
        cfg = {**_DEFAULTS, **_load_yaml(path)}
    cfg["key"] = name
    cfg.setdefault("name", name.replace("_", " ").title())
    # normalize the render budget into a tuple
    rb = cfg.get("render_budget") or cfg.get("lane_budgets", {}).get("render")         or _DEFAULTS["render_budget"]
    try:
        cfg["render_budget"] = (int(rb[0]), int(rb[1]))
    except Exception:
        cfg["render_budget"] = tuple(_DEFAULTS["render_budget"])
    return cfg


def format_spec(cfg: dict) -> dict:
    """Resolve output format: Shorts (vertical 9:16, <=60s) vs long (16:9).

    Shortsmith defaults to Shorts. Fields drive the whole pipeline (canvas dims,
    cutter aspect + duration cap, script scene-count bounds).
    """
    fmt = str(cfg.get("format", "shorts")).lower()
    is_shorts = fmt == "shorts"
    vertical = str(cfg.get("orientation", "vertical" if is_shorts else "horizontal")).lower() == "vertical"
    if is_shorts:
        default_bounds, default_max, default_target = [5, 8], 58, 42
    else:
        secs = int(cfg.get("target_minutes", 6)) * 60
        default_bounds, default_max, default_target = [8, 15], secs + 120, secs
    bounds = cfg.get("scene_bounds", default_bounds)
    try:
        bounds = (int(bounds[0]), int(bounds[1]))
    except Exception:
        bounds = tuple(default_bounds)
    return {
        "format": fmt,
        "vertical": vertical,
        "width": 1080 if vertical else 1920,
        "height": 1920 if vertical else 1080,
        "max_seconds": int(cfg.get("max_seconds", default_max)),
        "min_seconds": int(cfg.get("min_seconds", 0) or 0),
        "voice_speed": float(cfg.get("voice_speed", 1.18) or 1.18),
        "target_seconds": int(cfg.get("target_seconds", default_target)),
        # exact_seconds > 0 => final video is EXACTLY this long (speed-fit if
        # over, extend the last scene over the background if under)
        "exact_seconds": int(cfg.get("exact_seconds", 0) or 0),
        "scene_bounds": bounds,
    }


def render_budget(cfg: dict) -> tuple[int, int]:
    """How many scenes may use the optional AI-video provider, if one is set up.

    This used to be keyed by "lane" (night vs day). There is one render slot
    now, so it is just a property of the channel.
    """
    val = cfg.get("render_budget") or _DEFAULTS["render_budget"]
    try:
        return (int(val[0]), int(val[1]))
    except Exception:
        return (3, 15)


def lane_budget(cfg: dict, lane: str = "render") -> tuple[int, int]:
    """Deprecated alias kept so older call sites keep working."""
    return render_budget(cfg)


# --------------------------------------------------------------------------- #
# Hardware detection
# --------------------------------------------------------------------------- #
def _ram_bytes() -> tuple[int, int]:
    """Return (total_bytes, available_bytes). Best-effort, cross-platform."""
    # 1) psutil if present
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        return int(vm.total), int(vm.available)
    except Exception:
        pass
    # 2) Windows via ctypes
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            return int(stat.ullTotalPhys), int(stat.ullAvailPhys)
    except Exception:
        pass
    # 3) Linux /proc/meminfo
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            info[k.strip()] = int(v.strip().split()[0]) * 1024
        return info.get("MemTotal", 0), info.get("MemAvailable", info.get("MemFree", 0))
    except Exception:
        pass
    return 0, 0


def detect_free_ram_gb() -> float:
    _, avail = _ram_bytes()
    return round(avail / (1024 ** 3), 1) if avail else 8.0


def detect_total_ram_gb() -> float:
    total, _ = _ram_bytes()
    return round(total / (1024 ** 3), 1) if total else 8.0
