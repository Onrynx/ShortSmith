"""StoryForge Discord interface — THE control surface (replaces the PWA).

Outbound events go via DISCORD_WEBHOOK_URL (notify/push.py, no bot required).
This bot adds two-way control from any Discord client (phone included):

  !status                     what's rendering right now / what's ready
  !brain                      Odysseus / LLM backend status
  !pending                    list videos awaiting review
  !review <n|id>              thumbnail + script + report for one item
  !approve <n|id>             move to approved/
  !reject <n|id>              delete
  !cancel                     stop the render in progress
  !run <channel> [mock]       start a render (or just say "make me a short")
  !digest                     activity digest
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

MAX_UPLOAD = int(9.5 * 1024 * 1024)  # safe under a non-boosted server's ~10MB cap


def _fmt_item(i: int, m: dict) -> str:
    return (f"`{i}` **{m.get('title','?')}** — {m.get('channel','')}"
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

    from discord.ext import tasks

    from .utils import STATE_DIR, read_json, write_json
    SEEN_FILE = STATE_DIR / "discord_seen.json"

    @tasks.loop(seconds=30)
    async def watch_pending():
        """Auto-post every newly finished Short to the home channel."""
        ch = bot.get_channel(home_channel()) if home_channel() else None
        if not ch:
            return
        seen = set(read_json(SEEN_FILE, []) or [])
        items = control.list_pending()
        cur = {m["id"] for m in items}
        if not SEEN_FILE.exists():          # first start: baseline, don't spam history
            write_json(SEEN_FILE, sorted(cur))
            return
        new = [m for m in items if m["id"] not in seen]
        for m in reversed(new):
            try:
                # compress off the event loop so the bot stays responsive
                pv = await asyncio.to_thread(control.discord_preview, m["id"], MAX_UPLOAD)
                t = control.media_path(m["id"], "thumb")
                files = [discord.File(str(pv))] if pv else ([discord.File(str(t))] if t else [])
                v = control.media_path(m["id"], "video")
                note = "" if pv else (f"\n📁 full file: `{v}`" if v else "")
                await ch.send(f"🎬 **New Short ready:** {m.get('title')} "
                              f"({m.get('channel','')}, {round(m.get('runtime_sec') or 0)}s)"
                              f"{note}\n→ `!approve 1` to keep · `!reject 1` to bin",
                              files=files or None)
                seen.add(m["id"])                 # mark seen ONLY after a successful post
                write_json(SEEN_FILE, sorted(seen))
            except Exception as exc:  # noqa: BLE001 — leave unseen so it retries next tick
                log("warn", f"auto-post failed for {m['id']}: {exc}")

    @watch_pending.before_loop
    async def _wp_wait():
        await bot.wait_until_ready()

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("That needs a value — e.g. `!review 1`, `!run gossip_stories`. `!help` lists them.")
            return
        log("warn", f"command error: {error}")
        try:
            await ctx.send(f"⚠️ {error}")
        except Exception:
            pass

    @bot.event
    async def on_ready():
        log("ok", f"Discord bot online as {bot.user} (channel filter: {chan_id or 'all'})")
        if not watch_pending.is_running():
            watch_pending.start()

    # ---- natural-language chat (free local LLM via nl_agent) ---- #
    import asyncio

    from . import nl_agent
    admin_ids = {int(x) for x in (env("DISCORD_ADMIN_IDS", "") or "").replace(" ", "").split(",") if x}
    from .utils import STATE_DIR
    HOME_FILE = STATE_DIR / "discord_home.txt"

    def home_channel() -> int:
        if chan_id:
            return chan_id
        try:
            return int(HOME_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            return 0

    @bot.command()
    async def here(ctx):
        """Make THIS channel Valentine's home — chat here with no @mention."""
        if allowed(ctx):
            HOME_FILE.write_text(str(ctx.channel.id), encoding="utf-8")
            await ctx.send("💬 This is now my home channel — just talk to me here, no @ needed.")

    @bot.command(name="leave")
    async def leave_home(ctx):
        """Stop auto-replying in the home channel (back to @mention / DM only)."""
        if allowed(ctx):
            try:
                HOME_FILE.unlink()
            except Exception:
                pass
            await ctx.send("Okay — @mention me or DM me to chat now.")

    @bot.event
    async def on_message(msg):
        if msg.author.bot:
            return
        await bot.process_commands(msg)          # keep !commands working
        if msg.content.startswith("!"):
            return
        is_dm = msg.guild is None
        mentioned = bot.user in msg.mentions
        in_home = msg.channel.id == home_channel()
        if not (is_dm or mentioned or in_home):  # only talk when talked to
            return
        text = msg.content.replace(f"<@{bot.user.id}>", "").strip() or "hello"
        # empty allowlist = single-user mode: everyone is admin (set DISCORD_ADMIN_IDS to lock down)
        is_admin = (not admin_ids) or (msg.author.id in admin_ids)
        async with msg.channel.typing():
            reply = await asyncio.to_thread(nl_agent.handle, text, msg.channel.id, is_admin)
        await msg.channel.send(reply)

    @bot.command()
    async def help(ctx):
        if allowed(ctx):
            await ctx.send("**StoryForge** — just say **make me a short**. Commands: "
                           "`!status` `!pending` `!review n` `!approve n` `!reject n` "
                           "`!cancel` `!run <channel> [mock]` `!brain` `!digest` `!feed [n]`")

    @bot.command()
    async def status(ctx):
        if not allowed(ctx):
            return
        from . import nl_agent
        await ctx.send(nl_agent._render_progress())

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
    async def review(ctx, arg: str = "1"):
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
        video = control.discord_preview(item_id, MAX_UPLOAD)  # master or compressed preview
        if video:
            files.append(discord.File(str(video)))
        note = "" if video else \
            f"\n(preview failed — full file at `{control.media_path(item_id, 'video')}`)"
        await ctx.send(f"**{m.get('title')}** ({m.get('channel','')}, "
                       f"{round(m.get('runtime_sec') or 0)}s)\n{script[:1500]}{note}\n"
                       f"`!approve {arg}` or `!reject {arg}`", files=files or None)

    @bot.command()
    async def approve(ctx, arg: str = "1"):
        if not allowed(ctx):
            return
        item_id = _resolve(arg)
        r = control.approve(item_id) if item_id else {"ok": False, "error": "not found"}
        await ctx.send("✅ approved → approved/" if r.get("ok") else f"❌ {r.get('error')}")

    @bot.command()
    async def reject(ctx, arg: str = "1"):
        if not allowed(ctx):
            return
        item_id = _resolve(arg)
        r = control.reject(item_id) if item_id else {"ok": False, "error": "not found"}
        await ctx.send("🗑️ rejected & deleted" if r.get("ok") else f"❌ {r.get('error')}")

    @bot.command()
    async def run(ctx, channel: str = "gossip_stories", mode: str = ""):
        if not allowed(ctx):
            return
        r = control.run_now(channel, mock=(mode == "mock"))
        await ctx.send(f"▶ started (pid {r['pid']})" if r.get("ok") else f"❌ {r.get('error')}")

    @bot.command()
    async def cancel(ctx):
        if not allowed(ctx):
            return
        r = control.cancel_render()
        if r.get("killed"):
            await ctx.send("🛑 Stopped the render in progress.")
        else:
            await ctx.send("Nothing is rendering right now.")

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
            f"{icon.get(e.get('severity'), '•')} [{e.get('channel','')}] "
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
