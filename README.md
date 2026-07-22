# 🎬 StoryForge

A personal, fully-autonomous, multi-agent **YouTube Shorts** studio for one user.
Two AI-generated vertical Shorts a day, on two engine-isolated lanes, seven agents,
one voice, human-approved before anything goes public — all free, default local,
and **runs end-to-end on the first try with zero keys, zero network, zero GPU.**

- 📱 **Vertical 9:16 Shorts, 2.5–2.9 min** — conflict in the very first sentence,
  word-by-word pop captions, random length each run, capped under YouTube's 3-min limit.
- 🎮 **The classic Shorts format** — continuous gameplay (Minecraft parkour) behind the
  narration, random clip + random section every video, auto-cropped to 9:16. Falls back
  to AI video (Wan2GP/cloud) → stock → free AI images → generative motion. Never fails.
- 🧠 **Runs on PewDiePie's [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus)** —
  his self-hosted AI workspace — as the preferred brain (own your AI, don't rent it),
  falling back to raw Ollama → Gemini → a canned generator. A run never hard-fails.
- ✍️ **An enforced story doctrine** — Reddit-confession style, first person, male narrator,
  one twist, comment-bait ending. Fantasy/AI-slop is mechanically rejected and rewritten.
- 🤖 **7 agents**: Scout · Scribe · Critic → script · voice · canvas · cutter · herald.
- 💬 **Discord is the interface** — events stream to your server via webhook; a bot
  gives `!status`, `!review`, `!approve`, `!run`, `!lanes` from any device.
- ✅ **Compliance baked in** for YouTube's 2025–2026 inauthentic-content policy.

---

> ## 📖 Setting this up? Use the full guide for your computer:
> ### → **[Windows: README_WINDOWS.md](README_WINDOWS.md)**
> ### → **[macOS: README_MAC.md](README_MAC.md)**
> Both assume zero coding experience and cover every dependency, the Discord bot,
> settings and troubleshooting. ([SETUP.md](SETUP.md) is the short version.)

---

## Design principle: degrade, never die

The core pipeline depends only on the **Python stdlib + numpy + Pillow + FFmpeg**.
Everything heavy — Odysseus/Ollama, CrewAI, LangGraph, Kokoro TTS, faster-whisper,
FastAPI, pywebpush, Wan2GP, cloud APIs — is **optional and guarded**: real when
present, graceful fallback when absent. Every stage has a fallback, so one flaky
layer never kills a lane.

---

## Architecture

```
scheduler ──> daily lane picker (Discord !lanes, 30-min cutoff → reuse yesterday)
    │
    ├── LANE A (night / local)  channel → Wan2GP hero clips @480p + stock/motion
    └── LANE B (day / cloud)    channel → Veo/Seedance hero clips + stock/motion
                                    │
   CrewAI creative crew  ─────► [ script ] ─► voice ─► canvas ─► cutter ─► herald
   (Scout→Scribe→Critic,          (one LangGraph node)   (provider router)   (pending/)
    ONE LangGraph node)         └──────── LangGraph StateGraph (SqliteSaver) ───────┘
```

- **CrewAI** runs the collaborative creative crew (shared context).
- **LangGraph** runs the checkpointed assembly line — a crash mid-render resumes
  without re-running the expensive script/clip stages (a stdlib checkpointed
  runner takes over if LangGraph isn't installed).

The two channels are **Gossip Stories** (modern real-life drama — the main one) and
**Gaming Lore**. Either can be night or day; you assign lanes in Discord.

---

## Getting started

### 1) Run it in MOCK mode (4 commands — no keys, no network, no GPU)
```bash
cd "Youtube Ecosystem"
python -m pip install numpy pillow          # usually already present
# FFmpeg must be on PATH (winget install Gyan.FFmpeg  /  brew install ffmpeg  /  apt install ffmpeg)
python run.py --channel gossip_stories --lane night --mock
```
→ a real vertical **1080×1920 .mp4** + thumbnail + approval report land in
`output/gossip_stories/pending/`. Try `--channel gaming_lore --lane day --mock` too.

### 2) Add a local brain (Ollama)
```bash
# install Ollama (https://ollama.com), then:
ollama pull qwen2.5:14b        # or llama3.2:3b if you have < 16GB free RAM
```
StoryForge auto-detects Ollama and routes the crew through it. Drop `--mock` to use it.

### 3) Add PewDiePie's Odysseus (preferred brain)
```bash
bash scripts/setup_odysseus.sh     # clones + `docker compose up`, runs at :7000
# then in .env:  ODYSSEUS_URL=http://localhost:7000   ODYSSEUS_MODEL=qwen2.5:14b
```
Odysseus fronts your Ollama with an OpenAI-compatible `/v1` API; StoryForge prefers
it automatically. Verify: `python -c "from storyforge.llm import select_model; print(select_model())"`.

### 4) Add the NIGHT lane's local video engine (Wan2GP)
```bash
bash scripts/setup_wan2gp.sh       # clone + install; prints the low-VRAM 480p launch line
# launch it, then in .env:  WAN2GP_URL=http://localhost:7860
python run.py --channel gossip_stories --lane night
```

### 5) Add stock + cloud keys (free)
```
# .env
PEXELS_API_KEY=...      PIXABAY_API_KEY=...     # unlimited stock motion backfill
GEMINI_API_KEY=...                              # DAY lane hero clips via Veo (free tier)
CLOUD_DAILY_CAP=5                               # cap → stock backfill + 🟡 push when hit
```

### 6) Discord — the control surface
```bash
pip install discord.py
# .env:
#   DISCORD_WEBHOOK_URL=...   # channel webhook: 🔴🟡🟢 events, zero deps
#   DISCORD_BOT_TOKEN=...     # bot from discord.com/developers (Message Content intent ON)
#   DISCORD_CHANNEL_ID=...    # optional: restrict commands to one channel
python -m storyforge.discord_bot
```
Commands: `!status` `!brain` `!pending` `!review n` `!approve n` `!reject n`
`!run <ch> <lane> [mock]` `!daily [mock]` `!lanes <night> <day>` `!digest` `!feed`.

### 7) The daily flow
- Cron runs `python -m storyforge.scheduler picker` → Discord asks you to assign
  today's lanes. Reply `!lanes history_mysteries gaming_lore` (or swap).
- No answer within `LANE_PICK_CUTOFF_MIN` (30m) → yesterday's mapping is reused.
- Each finished Short lands in pending → `!pending`, `!review 1`, then `!approve 1`
  (moves to `approved/`) or `!reject 1` (deletes). Approval stays required.

### 8) Enable auto-upload (optional, always PRIVATE)
```
pip install -r requirements-cloud.txt
# .env:  PUBLISH=true   YOUTUBE_CLIENT_SECRETS=client.json   YOUTUBE_TOKEN_JSON=token.json
python run.py --channel gossip_stories --lane night --publish
```
Uploads **private** with the self-declared synthetic-content disclosure set. Never public.

---

## CLI & scheduler

```bash
python run.py --channel <key> --lane {night|day} [--mock] [--topic "..."] [--publish]
python run.py --list

python -m storyforge.scheduler picker                 # push "assign lanes"
python -m storyforge.scheduler set --night gossip_stories --day gaming_lore
python -m storyforge.scheduler daily   [--mock]       # ensure assignment, run both lanes
python -m storyforge.scheduler dispatch --lane night  # run today's night channel
python -m storyforge.scheduler digest                 # push the nightly digest
```

## Channels & format
`config/*.yaml` — niche, angle, Kokoro voice, banned list, per-lane hero budgets,
and format (`shorts` vertical 9:16 by default; set `format: long` for 16:9 long-form).

## Compliance
See [COMPLIANCE.md](COMPLIANCE.md). AI disclosure in every description, no invented
facts, no copyrighted IP in prompts, synthetic-content flag on upload, and a
mandatory human approval gate.
