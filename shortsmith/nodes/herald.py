"""Stage 5 — herald.

Finalizes title/description/tags (AI disclosure always appended), renders a
thumbnail (first hero frame + bold headline), and writes the finished mp4 +
approval_report.txt + a metadata sidecar into output/<channel>/pending/ for human
review. If PUBLISH=true and YouTube creds exist, uploads PRIVATE with the
self-declared synthetic-content disclosure. NEVER auto-public.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..config import format_spec
from ..notify import write_run_report
from ..schema import DISCLOSURE_LINE, ensure_disclosure
from ..state import pending_dir, run_basename
from ..utils import env, env_bool, run_ffmpeg, utcnow_iso
from .common import add_error, add_fallback, mark_done, progress

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\Arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _load_font(size: int):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _wrap_headline(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) > max_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines[:3]


def _base_thumb_image(state, workdir: Path, tw: int, th: int):
    from PIL import Image
    scenes = state["script"]["scenes"]
    visuals = state.get("visual_paths", [])
    kinds = state.get("visual_kinds", [])
    hero_idx = next((i for i, s in enumerate(scenes) if s.get("hero")), 0)
    src = visuals[hero_idx] if hero_idx < len(visuals) else (visuals[0] if visuals else None)
    kind = kinds[hero_idx] if hero_idx < len(kinds) else "image"

    frame = workdir / "thumb_frame.png"
    if src and kind == "video":
        ok, _ = run_ffmpeg(["-ss", "0.6", "-i", str(Path(src).resolve()),
                            "-frames:v", "1", "-q:v", "2", str(frame.resolve())],
                           cwd=workdir)
        if ok and frame.exists():
            src = str(frame)
    try:
        img = Image.open(src).convert("RGB") if src else Image.new("RGB", (tw, th), (13, 17, 23))
    except Exception:
        img = Image.new("RGB", (tw, th), (13, 17, 23))
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    img = img.resize((int(iw * scale), int(ih * scale)))
    iw, ih = img.size
    img = img.crop(((iw - tw) // 2, (ih - th) // 2, (iw - tw) // 2 + tw, (ih - th) // 2 + th))
    return img


def _make_thumbnail(state, workdir: Path, dest: Path) -> str | None:
    try:
        from PIL import Image, ImageDraw
        spec = format_spec(state.get("channel_cfg", {}))
        tw, th = (spec["width"], spec["height"]) if spec["vertical"] else (1280, 720)
        img = _base_thumb_image(state, workdir, tw, th)

        # darken bottom for legibility
        grad = Image.new("L", (1, th), 0)
        start = int(th * 0.45)
        for y in range(th):
            grad.putpixel((0, y), int(210 * max(0, (y - start) / (th - start))))
        alpha = grad.resize((tw, th))
        img = Image.composite(Image.new("RGB", (tw, th), (0, 0, 0)), img, alpha)
        draw = ImageDraw.Draw(img, "RGBA")

        cfg = state.get("channel_cfg", {})
        badge = str(cfg.get("name", state.get("channel", ""))).upper()
        bfont = _load_font(int(th * 0.042))
        pad = int(tw * 0.02)
        draw.rectangle([pad, pad, pad * 2 + int(draw.textlength(badge, font=bfont)) + pad,
                        pad + int(th * 0.06)], fill=(220, 40, 40, 235))
        draw.text((pad * 1.6, pad * 1.2), badge, font=bfont, fill=(255, 255, 255, 255))

        title = state["script"]["title"]
        fsize = int(th * 0.058) if spec["vertical"] else 74
        font = _load_font(fsize)
        line_h = int(fsize * 1.15)
        lines = _wrap_headline(draw, title, font, tw - int(tw * 0.08))
        bottom_margin = int(th * 0.16) if spec["vertical"] else 40
        y = th - bottom_margin - len(lines) * line_h
        x = int(tw * 0.04)
        for line in lines:
            for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
            draw.text((x, y), line, font=font, fill=(255, 214, 10, 255))
            y += line_h

        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=90)
        return str(dest)
    except Exception as exc:  # noqa: BLE001
        add_fallback(state, "herald", f"thumbnail generation failed ({exc})")
        return None


# --------------------------------------------------------------------------- #
# Hashtags + publish-mode
# --------------------------------------------------------------------------- #
def build_hashtags(tags: list[str], is_shorts: bool = True, limit: int = 5) -> str:
    """Turn tags into YouTube hashtags (#Shorts first). Shown above the title."""
    out: list[str] = ["#Shorts"] if is_shorts else []
    for t in tags:
        camel = "".join(w.capitalize() for w in re.split(r"[^A-Za-z0-9]+", t) if w)
        h = "#" + camel
        if camel and h not in out:
            out.append(h)
        if len(out) >= limit:
            break
    return " ".join(out)


def publish_mode(state: dict) -> str:
    """Resolve upload behaviour: 'off' | 'private' | 'public'.

    AUTO_PUBLISH=public is the ONLY way videos go public automatically, and it is
    opt-in by you. Everything else uploads PRIVATE at most, and the default keeps
    the human approval gate (nothing is uploaded until you approve/--publish)."""
    ap = (env("AUTO_PUBLISH", "off") or "off").lower()
    if ap in ("private", "public"):
        return ap
    if state.get("publish") or env_bool("PUBLISH", False):
        return "private"
    return "off"


# --------------------------------------------------------------------------- #
# Optional YouTube upload (default PRIVATE; public only on explicit opt-in)
# --------------------------------------------------------------------------- #
def _upload_youtube(state, video: Path, thumb: str | None, title, description, tags,
                    privacy: str = "private") -> str | None:
    secrets = env("YOUTUBE_CLIENT_SECRETS")
    token = env("YOUTUBE_TOKEN_JSON")
    if not secrets or not token or not Path(token).exists():
        add_fallback(state, "herald", "publish requested but YouTube creds missing; kept in pending/")
        return None
    if privacy == "public":
        add_fallback(state, "herald", "AUTO_PUBLISH=public — uploading PUBLIC (fully autonomous)")
    try:
        from google.oauth2.credentials import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.http import MediaFileUpload  # type: ignore

        creds = Credentials.from_authorized_user_file(token, ["https://www.googleapis.com/auth/youtube.upload"])
        yt = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {"title": title[:100], "description": description,
                        "tags": tags, "categoryId": "27"},
            # Default PRIVATE; 'public' only when you explicitly set AUTO_PUBLISH=public.
            "status": {"privacyStatus": privacy if privacy in ("private", "public", "unlisted") else "private",
                       "selfDeclaredMadeForKids": False},
        }
        # Self-declared "altered or synthetic content" disclosure (best-effort;
        # field name may vary by API version — safe to ignore if rejected).
        try:
            body["status"]["containsSyntheticMedia"] = True
        except Exception:
            pass
        req = yt.videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(str(video), resumable=True))
        resp = req.execute()
        vid = resp.get("id")
        if thumb and vid:
            try:
                yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumb)).execute()
            except Exception:
                pass
        mark_done(state, "herald", f"uploaded {privacy.upper()} to YouTube: {vid}")
        return vid
    except Exception as exc:  # noqa: BLE001
        add_error(state, "herald", f"YouTube upload failed ({exc}); kept in pending/")
        return None


# --------------------------------------------------------------------------- #
# Node
# --------------------------------------------------------------------------- #
def node_herald(state: dict) -> dict:
    workdir = Path(state["workdir"])
    script = state["script"]
    channel = state["channel"]
    progress(state, "herald", "finalizing metadata, thumbnail, and approval package")

    spec = format_spec(state.get("channel_cfg", {}))

    # Posting metadata is written HERE, from the FINISHED script — after the
    # length fit and the continuity repair. The writer's own title was produced
    # mid-draft and can end up describing a version that no longer exists.
    from ..metadata import build as build_metadata
    meta = build_metadata(script, state.get("model_info"), state.get("plan"))
    state["metadata"] = meta

    title = meta.get("title") or script["title"]
    tags = meta.get("tags") or script.get("tags", [])
    hashtags = meta.get("hashtags") or build_hashtags(
        tags, is_shorts=(spec["format"] == "shorts"))
    description = meta.get("description") or ensure_disclosure(
        script.get("description", ""))
    if state.get("channel_cfg", {}).get("fiction") and "fiction" not in description.lower():
        description += "\nThis is a fictional story created for entertainment."

    base = run_basename(state)
    pend = pending_dir(channel)
    final_video = pend / f"{base}.mp4"
    report_path = pend / f"{base}.report.txt"
    thumb_path = pend / f"{base}.jpg"
    meta_path = pend / f"{base}.json"

    # thumbnail
    thumb = _make_thumbnail(state, workdir, thumb_path)

    # move the assembled mp4 into pending/
    src_video = Path(state.get("video_path", ""))
    if src_video.exists():
        try:
            shutil.copy2(src_video, final_video)
        except Exception as exc:  # noqa: BLE001
            add_error(state, "herald", f"could not place final mp4 in pending ({exc})")
            raise
    else:
        add_error(state, "herald", "cutter produced no video_path")
        raise RuntimeError("no video to herald")

    state["final_video_path"] = str(final_video)
    state["thumbnail_path"] = thumb or ""
    state["title"] = title
    state["description"] = description
    state["tags"] = tags

    # approval report
    write_run_report(state, report_path)
    state["report_path"] = str(report_path)

    # metadata sidecar the Discord bot reads for !review
    from ..utils import write_json
    runtime = round(sum(state.get("durations", [])), 1)
    # Upload behaviour resolved by publish_mode() (off|private|public). Default 'off'
    # => stays in pending/ for human approval. _upload_youtube no-ops without creds.
    mode = publish_mode(state)
    published_id = None
    if mode != "off":
        published_id = _upload_youtube(state, final_video, thumb, title, description, tags, privacy=mode)

    write_json(meta_path, {
        "run_id": state.get("run_id"),
        "channel": channel,
        "channel_name": state.get("channel_cfg", {}).get("name", channel),
        "lane": state.get("lane"),
        "mock": state.get("mock"),
        "publish_mode": mode,
        "hashtags": hashtags,
        # Everything needed to post THIS video, kept on THIS video's record so
        # two shorts on the same topic never share metadata.
        "metadata": meta,
        "format": spec["format"],
        "orientation": "vertical" if spec["vertical"] else "horizontal",
        "resolution": f"{spec['width']}x{spec['height']}",
        "created": utcnow_iso(),
        "title": title,
        "description": description,
        "tags": tags,
        "hook": script.get("hook"),
        "scenes": script.get("scenes"),
        "durations": state.get("durations", []),
        "runtime_sec": runtime,
        "hero_results": state.get("hero_results", {}),
        "fallbacks": state.get("fallbacks", []),
        "script_source": state.get("script_source"),
        "captions_kind": state.get("captions_kind"),
        "video": final_video.name,
        "thumbnail": thumb_path.name if thumb else None,
        "report": report_path.name,
        "disclosure": DISCLOSURE_LINE,
        "published_youtube_id": published_id,
        "status": "pending",
    })

    state["published"] = bool(published_id)
    mark_done(state, "herald",
              f"'{title}' -> pending/ ({runtime}s){' · uploaded PRIVATE' if published_id else ''}")
    return {
        "final_video_path": str(final_video),
        "thumbnail_path": thumb or "",
        "report_path": str(report_path),
        "title": title, "description": description, "tags": tags,
        "published": bool(published_id),
    }
