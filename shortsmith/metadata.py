"""Upload metadata, written from the video that actually exists.

The writer already produces a title and some tags, but it does that mid-draft —
before the length fit, the continuity repair and any rewrites have happened. So
the metadata describes a draft that may no longer match the finished video, and
the "description" ends up being little more than the hashtags again.

This module runs once the script is final and generates everything you need to
post, from that video's own words:

    titles        five options, so there is something to choose between
    description   a real description, with the AI disclosure appended
    hashtags      the handful YouTube shows above a Short
    tags          the longer keyword list
    caption       one line for posting elsewhere

Everything is generated locally and stored on the video's own record, so two
videos about the same topic never share metadata. There is no network call and
no API key: the finished script is better input than any trending list, because
it knows what this particular video actually says.
"""
from __future__ import annotations

import json
import re

from .llm import complete
from .schema import ensure_disclosure
from .utils import log

# What makes a Short's title work. These patterns are stable craft, which is why
# they can ship with the app instead of being fetched from somewhere.
_TITLE_CRAFT = """WHAT MAKES A SHORT'S TITLE WORK:
- An open loop: say enough to raise a question, never enough to answer it.
- Concrete nouns over abstractions. "My brother sold my car" beats "family betrayal".
- Front-load it. The first three or four words carry the click; the rest is lost
  to truncation.
- Second person or first person. "I" and "you" outperform "a man" and "people".
- A number, if the story genuinely has one.
- No clickbait punctuation, no ALL CAPS, no "you won't believe".
- Under 70 characters so nothing is cut off."""

_SYS = (
    "You write the posting metadata for short vertical videos. You work only "
    "from the script you are given — never invent events, names or numbers "
    "that are not in it. You return strict JSON only."
)


def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text or "")
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON in metadata reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON in metadata reply")


def _tag_to_hashtag(tag: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", str(tag))
    return "#" + "".join(p.capitalize() for p in parts) if parts else ""


def hashtags_from(tags: list, limit: int = 5, shorts: bool = True) -> str:
    """#Shorts first, then the strongest tags — what sits above the title."""
    out = ["#Shorts"] if shorts else []
    for t in tags or []:
        h = _tag_to_hashtag(t)
        if h and h.lower() not in [x.lower() for x in out]:
            out.append(h)
        if len(out) >= limit:
            break
    return " ".join(out)


def _fallback(script: dict) -> dict:
    """Usable metadata with no model — never leaves a video without any."""
    title = str(script.get("title") or "Untitled")
    tags = [t for t in (script.get("tags") or []) if str(t).strip()][:12]
    opening = str((script.get("scenes") or [{}])[0].get("narration", ""))[:180]
    return {
        "titles": [title],
        "title": title,
        "description": ensure_disclosure(opening),
        "hashtags": hashtags_from(tags),
        "tags": tags,
        "caption": opening[:120],
        "source": "fallback",
    }


def build(script: dict, model_info: dict | None = None,
          plan: dict | None = None, timeout: float = 420) -> dict:
    """Generate posting metadata from the FINISHED script."""
    scenes = script.get("scenes") or []
    if not scenes:
        return _fallback(script)

    full = " ".join(str(s.get("narration", "")) for s in scenes).strip()
    if not model_info or model_info.get("backend") == "mock":
        return _fallback(script)

    kind = (plan or {}).get("brief_kind", "")
    angle = ("This is an explainer about a real subject — the metadata must be "
             "accurate and must not sensationalise."
             if kind in ("factual", "historical") else
             "This is a short story. The metadata should tease it, never spoil "
             "the ending.")

    user = f"""Here is the complete narration of a finished vertical short video.

\"\"\"{full[:4000]}\"\"\"

{angle}

{_TITLE_CRAFT}

Write the posting metadata for THIS video, using only what the narration above
actually says.

Return ONLY strict JSON:
{{"titles": ["five different title options"],
  "description": "2-4 sentences describing this video, no hashtags",
  "tags": ["8-12 lowercase keyword tags"],
  "caption": "one short line for posting elsewhere"}}"""

    try:
        data = _extract_json(complete(_SYS, user, model_info, json_mode=True,
                                      temperature=0.8, timeout=timeout))
    except Exception as exc:  # noqa: BLE001
        log("warn", f"metadata generation failed ({exc}); using the script's own")
        return _fallback(script)

    titles = [str(t).strip() for t in (data.get("titles") or []) if str(t).strip()]
    titles = [t[:95] for t in titles][:5] or [str(script.get("title") or "Untitled")]
    tags = [str(t).strip().lower() for t in (data.get("tags") or []) if str(t).strip()][:12]
    desc = str(data.get("description") or "").strip()
    hashes = hashtags_from(tags)

    # Hashtags sit above the title on a Short, so they lead the description.
    body = f"{hashes}\n\n{desc}" if hashes else desc
    return {
        "titles": titles,
        "title": titles[0],
        "description": ensure_disclosure(body),
        "hashtags": hashes,
        "tags": tags,
        "caption": str(data.get("caption") or "").strip()[:200],
        "source": "generated",
    }
