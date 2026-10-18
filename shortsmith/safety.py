"""Two checks that every path built from outside input has to pass.

Identifiers reach this app from HTTP requests and from JSON files on disk, and
both end up concatenated into filesystem paths — `output/<channel>/pending/
<id>.json`, `.shortsmith_state/gate/<run_id>.answer.json`. Concatenation is the
dangerous part: an id of "../../../../x" is a perfectly ordinary Python string
right up until it becomes a path, and `Path.exists()` will happily walk it.

Two separate guards, because each catches what the other misses:

    safe_id()   rejects the input before it is ever joined. Cheap, and it
                refuses the whole class rather than one encoding of it.
    inside()    checks the RESOLVED path still sits under the directory it was
                supposed to. Catches symlinks, Windows 8.3 short names, drive
                letters and anything the first check did not imagine.

Neither is sufficient alone. A filename can be legal and still resolve
somewhere else; a path can resolve correctly and still have arrived by a route
nothing should be using.
"""
from __future__ import annotations

import re
from pathlib import Path

# Ids this app generates: "<channel>_<lane>_<slug>_<timestamp>_<lane>" and
# "<timestamp>_<lane>". Dots are allowed because some records carry a suffix,
# but ".." never is, and nothing may begin with a dot.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
MAX_ID = 200


def safe_id(value: str | None) -> str | None:
    """The id, or None if it could not be used in a path safely.

    Returning None rather than raising keeps every caller's error path the one
    it already had: "not found". An id that fails this check does not name
    anything the user can reach, so that is the honest answer as well as the
    convenient one.
    """
    if not value:
        return None
    text = str(value)
    if len(text) > MAX_ID:
        return None
    if ".." in text:
        return None
    # Separators and the Windows drive colon never belong in an identifier.
    if any(c in text for c in ("/", "\\", ":", "\0")):
        return None
    return text if _ID.match(text) else None


def inside(path: Path | str, root: Path | str) -> bool:
    """True when `path` really resolves to somewhere under `root`."""
    try:
        resolved = Path(path).resolve()
        base = Path(root).resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    return resolved == base or resolved.is_relative_to(base)


def contained(root: Path | str, name: str | None) -> Path | None:
    """`root / name`, but only if `name` is a safe id and it lands inside."""
    ident = safe_id(name)
    if ident is None:
        return None
    candidate = Path(root) / ident
    return candidate if inside(candidate, root) else None


__all__ = ["safe_id", "inside", "contained", "MAX_ID"]
