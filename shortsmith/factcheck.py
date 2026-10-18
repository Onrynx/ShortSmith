"""Checking the facts in an explainer, with nothing but the model on this laptop.

Explainer mode has one failure that story mode does not: a 14B model will state
something false with total confidence. A real run of ours produced the sentence
"The Earth rotates east to west" inside an otherwise good explanation of the
Coriolis effect. Nothing in the pipeline noticed, because nothing was looking.

There is no internet here by design, so this cannot be a lookup. What it can be
is a second opinion, asked properly:

1.  Pull the checkable claims out of the finished script. Not opinions, not
    metaphors — statements that are either true or false.
2.  Ask about each claim ON ITS OWN, with no story around it and nothing to be
    consistent with. A model defending a paragraph it just wrote will agree with
    itself; the same model shown one bare sentence often will not.
3.  Ask twice, at a temperature low enough to be near-deterministic, and only
    trust the two answers where they agree. Disagreement is the useful signal:
    it means the model does not actually know.

What comes out is a verdict per claim — ok, wrong (with a correction), or
unsure. Wrong claims that both passes agree on are corrected in the script.
Everything else is surfaced to you rather than silently changed.

This reduces confident errors. It does not eliminate them, and it cannot: a
model that is wrong the same way twice will pass. Treat the result as a second
reader who knows a lot and is sometimes mistaken, which is exactly what it is.
"""
from __future__ import annotations

import json
import re

from .llm import complete
from .utils import log

_EXTRACT_SYS = (
    "You pull checkable factual claims out of text. A checkable claim is "
    "something the text ASSERTS AS TRUE about the world — a number, a date, a "
    "name, a mechanism, a cause. You return strict JSON only."
)

_VERIFY_SYS = """You are a careful fact-checker for a short video aimed at a
general audience. You are shown ONE statement with no context.

"wrong" means a viewer would come away MISINFORMED — a reversed direction, a
wrong number, the wrong cause, a confused mechanism.

"ok" covers anything broadly true, including simplifications, round numbers and
everyday phrasing. A short video cannot carry every caveat, and demanding one
is not fact-checking. If your only objection is that the statement is
imprecise, incomplete or lacks nuance, the answer is ok.

"unsure" is for when you genuinely do not know.

Do not be agreeable, and do not be pedantic. You return strict JSON only."""


def _json_from(text: str) -> dict | list:
    text = re.sub(r"```(?:json)?", "", text or "")
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
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
            elif c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("no JSON in reply")


def extract_claims(script: dict, model_info: dict, limit: int = 10,
                   timeout: float = 300) -> list[dict]:
    """The statements in this script that are either true or false."""
    scenes = script.get("scenes") or []
    numbered = "\n".join(f"[{i}] {s.get('narration', '')}"
                         for i, s in enumerate(scenes))
    user = f"""Here is the narration of a short explainer video, one scene per line.

{numbered[:5000]}

List the checkable factual claims it makes. Quote each claim as a single
self-contained sentence that makes sense on its own — if the original says
"it spins the other way", write out what "it" is.

Skip:
- opinions, metaphors, and anything addressed to the viewer
- MYTHS AND BELIEFS THE VIDEO IS SETTING UP TO KNOCK DOWN. A line like "people
  will tell you X" or "everyone thinks X" is not claiming X is true — it is
  reporting a belief, usually right before refuting it. Read the lines that
  follow: if the video contradicts it, skip it.
- deliberate simplifications that are broadly right for a general audience

Return ONLY strict JSON:
{{"claims": [{{"scene": 0, "claim": "a self-contained factual statement"}}]}}"""
    try:
        data = _json_from(complete(_EXTRACT_SYS, user, model_info, json_mode=True,
                                   temperature=0.2, timeout=timeout))
    except Exception as exc:  # noqa: BLE001
        log("warn", f"could not extract claims: {exc}")
        return []
    out = []
    for c in (data.get("claims") if isinstance(data, dict) else data) or []:
        if not isinstance(c, dict):
            continue
        text = str(c.get("claim") or "").strip()
        if len(text) < 12:
            continue
        try:
            scene = int(c.get("scene", -1))
        except (TypeError, ValueError):
            scene = -1
        out.append({"scene": scene, "claim": text[:400]})
        if len(out) >= limit:
            break
    return out


def _ask_once(claim: str, model_info: dict, timeout: float) -> dict:
    user = f"""Statement: "{claim}"

Would a general viewer be misinformed by this? Remember that a simplification
is not an error.

Return ONLY strict JSON:
{{"verdict": "ok" | "wrong" | "unsure",
  "correction": "the accurate version, only if the verdict is wrong",
  "why": "one short sentence"}}"""
    data = _json_from(complete(_VERIFY_SYS, user, model_info, json_mode=True,
                               temperature=0.1, timeout=timeout))
    verdict = str((data or {}).get("verdict") or "unsure").lower()
    if verdict not in ("ok", "wrong", "unsure"):
        verdict = "unsure"
    return {"verdict": verdict,
            "correction": str((data or {}).get("correction") or "").strip()[:400],
            "why": str((data or {}).get("why") or "").strip()[:300]}


def verify(claim: str, model_info: dict, timeout: float = 180) -> dict:
    """Two independent passes. Only agreement counts."""
    try:
        first = _ask_once(claim, model_info, timeout)
        second = _ask_once(claim, model_info, timeout)
    except Exception as exc:  # noqa: BLE001
        log("warn", f"fact-check call failed: {exc}")
        return {"verdict": "unsure", "correction": "", "why": "could not be checked",
                "agreed": False}
    if first["verdict"] == second["verdict"]:
        best = first if first["correction"] else second
        return {**best, "verdict": first["verdict"], "agreed": True}
    # The two passes disagree, which is exactly the case where a single pass
    # would have sounded certain. Say so instead of picking one.
    return {"verdict": "unsure", "correction": "",
            "why": f"checked twice and got different answers "
                   f"({first['verdict']} then {second['verdict']})",
            "agreed": False}


def check_script(script: dict, model_info: dict, limit: int = 8,
                 timeout: float = 180) -> dict:
    """Extract and verify. Returns findings; changes nothing."""
    if not model_info or model_info.get("backend") == "mock":
        return {"ran": False, "claims": [], "wrong": [], "unsure": []}
    claims = extract_claims(script, model_info, limit=limit)
    if not claims:
        return {"ran": True, "claims": [], "wrong": [], "unsure": []}

    results = []
    for c in claims:
        r = verify(c["claim"], model_info, timeout=timeout)
        results.append({**c, **r})
    wrong = [r for r in results if r["verdict"] == "wrong" and r["correction"]]
    unsure = [r for r in results if r["verdict"] == "unsure"]
    log("info", f"fact-check: {len(results)} claims, {len(wrong)} wrong, "
                f"{len(unsure)} unsure")
    return {"ran": True, "claims": results, "wrong": wrong, "unsure": unsure}


def apply_corrections(script: dict, findings: dict, model_info: dict,
                      timeout: float = 300) -> int:
    """Rewrite the scenes that contain an agreed-wrong claim. Returns how many.

    Only the sentence is replaced, by rewriting that one scene — regenerating
    the whole script would throw away everything the length fitting and the
    continuity pass already got right.
    """
    wrong = findings.get("wrong") or []
    if not wrong:
        return 0
    scenes = script.get("scenes") or []
    fixed = 0
    for item in wrong:
        i = item.get("scene", -1)
        if not (0 <= i < len(scenes)):
            continue
        original = str(scenes[i].get("narration") or "")
        if not original:
            continue
        user = f"""This line of narration contains a factual error.

LINE: "{original}"

WHAT IS WRONG: {item['claim']}
THE ACCURATE VERSION: {item['correction']}

Rewrite the line so it is accurate. Keep the same length (within a few words),
the same tone, and the same role in the explanation. Change only what has to
change to make it true.

Return ONLY the rewritten line, with no quotes and no commentary."""
        try:
            new = complete("You correct factual errors in narration without "
                           "changing its voice or length.", user, model_info,
                           temperature=0.3, timeout=timeout).strip()
        except Exception as exc:  # noqa: BLE001
            log("warn", f"could not correct scene {i}: {exc}")
            continue
        new = new.strip().strip('"').strip()
        if not new:
            continue
        # A "correction" that is much longer wrecks the length fit the writer
        # just spent several passes achieving.
        if len(new.split()) > len(original.split()) * 1.25 + 4:
            log("warn", f"rejected an over-long correction for scene {i}")
            continue
        # A rewrite that keeps the original intact and bolts a hedge onto the
        # end has not corrected anything — it has just argued with the line.
        # That is how "people say X" became "people say X, but this is untrue"
        # one line before the script refuted X itself.
        stripped = original.rstrip(".!? ").lower()
        if new.lower().startswith(stripped):
            log("warn", f"rejected a hedge-on-the-end correction for scene {i}")
            continue
        scenes[i]["narration"] = new
        item["applied"] = True
        fixed += 1
    if fixed:
        log("info", f"fact-check corrected {fixed} line(s)")
    return fixed


__all__ = ["check_script", "apply_corrections", "extract_claims", "verify"]
