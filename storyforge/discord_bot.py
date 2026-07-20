"""StoryForge Discord interface — THE control surface (replaces the PWA).

Outbound events go via DISCORD_WEBHOOK_URL (notify/push.py, no bot required).
This bot adds two-way control from any Discord client (phone included):

  !status                     lanes, stages, cloud cap, pending count
  !brain                      Odysseus / LLM backend status
  !pending                    list videos awaiting review
  !review <n|id>              thumbnail + script + report for one item
  !approve <n|id>             move to approved/
  !reject <n|id>              delete
  !run <channel> <lane> [mock]
  !daily [mock]               run both lanes for today's assignment
  !lanes <night_ch> <day_ch>  assign today's lanes (the daily picker)
  !digest                     nightly digest
  !feed [n]                   recent bottleneck events

Setup: pip install discord.py ; create a bot (discord.com/developers), enable the
MESSAGE CONTENT intent, invite it to your server, then in .env:
  DISCORD_BOT_TOKEN=...   DISCORD_CHANNEL_ID=<channel id>   (0 = all channels)
Run:  python -m storyforge.discord_bot
"""
from __future__ import annotations

from pathlib import Path

from . import control
from .notify import nightly_digest
from .utils import env, log

MAX_UPLOAD = 24 * 1024 * 1024  # stay under Discord's 25MB default


def _fmt_item(i: int, m: dict) -> str:
    return (f"`{i}` **{m.get('title','?')}** — {m.get('channel')}/{m.get('lane')}"
            f" · {round(m.get('runtime_sec') or 0)}s{' · mock' if m.get('mock') else ''}")


def _resolve(arg: str) -> str | None:
    """Accept an index into !pending (1-based) or a full item id."""
    items = control.list_pending()
    if arg.isdigit() and 1 <= int(arg) <= len(items):
        return items[int(arg) - 1]["id"]
    return arg if control.find_item(arg) else None


def build_bot(chan_id: int = 0):
    """Construct the bot + all command handlers (no network). Testable offline."""
    import discord
    from discord.ext import commands

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

    def allowed(ctx) -> bool:
        return chan_id == 0 or ctx.channel.id == chan_id

    @bot.event
    async def on_ready():
        log("ok", f"Discord bot online as {bot.user} (channel filter: {chan_id or 'all'})")

    @bot.command()
    async def help(ctx):
        if allowed(ctx):
            await ctx.send("**StoryForge** — `!status` `!brain` `!pending` `!review n` "
                           "`!approve n` `!reject n` `!run <ch> <lane> [mock]` "
                           "`!daily [mock]` `!lanes <night> <day>` `!digest` `!feed [n]`")

    @bot.command()
    async def status(ctx):
        if not allowed(ctx):
            return
        s = control.status()
        lines = []
        for lane in ("night", "day"):
            st = s["lanes"].get(lane) or {}
            lines.append(f"**{lane}**: {st.get('stage','idle')} — {st.get('message','')[:120]}")
        a = s["assignment"]
        lines.append(f"lanes today: night={a.get('night','?')} day={a.get('day','?')}")
        lines.append(f"cloud cap left: {s['cloud_cap_remaining']} · pending review: {s['pending_count']}")
        await ctx.send("\n".join(lines))

    @bot.command()
    async def brain(ctx):
        if not allowed(ctx):
            return
        o = control.odysseus_status()
        b = o["storyforge_brain"]
        await ctx.send(f"Odysseus UI {'🟢' if o['ui_up'] else '🔴'} · /v1 "
                       f"{'🟢' if o['llm_v1_up'] else '🔴'} ({o['url']})\n"
                       f"brain: **{b['backend']}/{b['model']}** — {b['reason']}")

    @bot.command()
    async def pending(ctx):
        if not allowed(ctx):
            return
        items = control.list_pending()
        if not items:
            await ctx.send("Nothing awaiting review.")
            return
        await ctx.send("\n".join(_fmt_item(i + 1, m) for i, m in enumerate(items[:10])))

    @bot.command()
    async def review(ctx, arg: str):
        if not allowed(ctx):
            return
        item_id = _resolve(arg)
        m = control.get_pending(item_id) if item_id else None
        if not m:
            await ctx.send("Not found. Use `!pending` first.")
            return
        scenes = m.get("scenes") or []
        script = "\n".join(f"{i+1}. {'⭐' if s.get('hero') else '·'} {s.get('narration','')}"
                           for i, s in enumerate(scenes))
        files = []
        thumb = control.media_path(item_id, "thumb")
        if thumb:
            files.append(discord.File(str(thumb)))
        video = control.media_path(item_id, "video")
        if video and video.stat().st_size <= MAX_UPLOAD:
            files.append(discord.File(str(video)))
        note = "" if (video and video.stat().st_size <= MAX_UPLOAD) else \
            f"\n(video too big for Discord — at `{video}`)" if video else ""
        await ctx.send(f"**{m.get('title')}** ({m.get('channel')}/{m.get('lane')}, "
                       f"{round(m.get('runtime_sec') or 0)}s)\n{script[:1500]}{note}\n"
                       f"`!approve {arg}` or `!reject {arg}`", files=files or None)

    @bot.command()
    async def approve(ctx, arg: str):
        if not allowed(ctx):
            return
        item_id = _resolve(arg)
        r = control.approve(item_id) if item_id else {"ok": False, "error": "not found"}
        await ctx.send("✅ approved → approved/" if r.get("ok") else f"❌ {r.get('error')}")

    @bot.command()
    async def reject(ctx, arg: str):
        if not allowed(ctx):
            return
        item_id = _resolve(arg)
        r = control.reject(item_id) if item_id else {"ok": False, "error": "not found"}
        await ctx.send("🗑️ rejected & deleted" if r.get("ok") else f"❌ {r.get('error')}")

    @bot.command()
    async def run(ctx, channel: str, lane: str = "night", mode: str = ""):
        if not allowed(ctx):
            return
        r = control.run_now(channel, lane, mock=(mode == "mock"))
        await ctx.send(f"▶ started (pid {r['pid']})" if r.get("ok") else f"❌ {r.get('error')}")

    @bot.command()
    async def daily(ctx, mode: str = ""):
        if not allowed(ctx):
            return
        r = control.run_scheduler_daily(mock=(mode == "mock"))
        await ctx.send(f"▶ daily cycle started (pid {r['pid']})")

    @bot.command()
    async def lanes(ctx, night: str, day: str):
        if not allowed(ctx):
            return
        r = control.set_lanes(night, day)
        a = r["assignment"]
        await ctx.send(f"🌗 set — night: **{a.get('night')}** · day: **{a.get('day')}**")

    @bot.command()
    async def digest(ctx):
        if allowed(ctx):
            await ctx.send(f"```\n{nightly_digest()[:1900]}\n```")

    @bot.command()
    async def feed(ctx, n: str = "10"):
        if not allowed(ctx):
            return
        evs = control.events(int(n) if n.isdigit() else 10)
        icon = {"red": "🔴", "yellow": "🟡", "green": "🟢", "info": "•"}
        await ctx.send("\n".join(
            f"{icon.get(e.get('severity'), '•')} [{e.get('lane')}/{e.get('channel')}] "
            f"{e.get('stage')}: {e.get('message', '')[:100]}" for e in evs[:15]) or "no events")

    return bot


def main() -> int:
    try:
        import discord  # noqa: F401
    except ImportError:
        print("pip install discord.py  (then set DISCORD_BOT_TOKEN in .env)")
        return 1
    token = env("DISCORD_BOT_TOKEN")
    if not token:
        print("Set DISCORD_BOT_TOKEN in .env (create a bot at discord.com/developers,"
              " enable the Message Content intent).")
        return 1
    bot = build_bot(int(env("DISCORD_CHANNEL_ID", "0") or 0))
    bot.run(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
