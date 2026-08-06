# 🎬 StoryForge

A **free, fully-local** YouTube Shorts factory you run on your own laptop and
control by chatting with your own Discord bot. Ask for a video; a few minutes
later a finished vertical Short — original story, AI narration, gameplay
background, synced captions, music, thumbnail — posts itself back to you for
approval. Nothing is uploaded anywhere until *you* do it.

> ## 📖 Setting it up? Follow the guide for your computer — every step, zero coding assumed:
> ### → **[Windows: README_WINDOWS.md](README_WINDOWS.md)**
> ### → **[macOS: README_MAC.md](README_MAC.md)**
> Short version: **[SETUP.md](SETUP.md)**.

---

## What one video actually contains

- 📱 **Vertical 9:16, ~2.5–2.9 min** (a random length each run; capped under
  YouTube's 3-minute Shorts limit).
- ✍️ **An original story**, written by a local AI, in the viral Reddit-confession
  style — first person, male narrator, conflict in the first sentence, a twist,
  and a comment-bait ending. An enforced "story doctrine" rejects fantasy / AI
  slop and rewrites it; a memory of past videos prevents repeats.
- 🎙️ **AI narration** — the deep "Adam" voice or a high-pitched "squeaky" one
  (you pick in chat). Fast pacing (~177 wpm), the viral range.
- 🎮 **Gameplay background** — your own Minecraft-parkour clips, a *random clip
  and random section every time*, auto-cropped to 9:16.
- 🔤 **Word-by-word pop captions**, synced to the voice.
- 🎵 **Mood-matched background music**, picked to fit the story.
- 🖼️ **A thumbnail**, plus a report with the full script and a compliance checklist.

---

## How it works

```
You in Discord:  "make me a short"
        │                       Valentine (your bot) asks: Adam or squeaky?
        ▼
   Your laptop runs the 5-stage pipeline:
   script ─► voice ─► canvas ─► cutter ─► herald
     │        │         │         │         │
  local AI  Kokoro   parkour    FFmpeg:   thumbnail +
  (Ollama)  TTS      background  captions  saved to
  writes    narrates + crop 9:16 + music   output/…/pending/
        │
        ▼
   The finished video posts itself back into your Discord channel
        │
        ▼
   You:  !approve 1   (→ output/…/approved/)   or   !reject 1
```

**Discord is only the remote control.** All the writing, rendering and approving
happens on your machine — Discord needs almost no permissions, and if the token
ever leaked the worst anyone could do is post messages.

---

## What it's built from (and what's optional)

| Job | What actually runs by default | Free? |
|---|---|---|
| **Story writing** | one local model, **qwen2.5:14b via Ollama**, using a chunked-continuation loop + the enforced story doctrine + anti-repeat memory | ✅ local |
| **Orchestration** | a lightweight built-in pipeline runner (`run_lite`) | ✅ |
| **Voice** | **Kokoro** (`am_adam`), local, commercial-safe | ✅ local |
| **Caption sync** | **faster-whisper** (`tiny`) | ✅ local |
| **Visuals** | your gameplay clips + FFmpeg | ✅ local |
| Chat brain (the bot's replies) | Ollama `qwen3:8b` | ✅ local |

**Optional upgrades** (not installed, not required):

| Add | Get | How |
|---|---|---|
| `GEMINI_API_KEY` | far stronger + faster story writing | free Google key in `.env` |
| `ELEVENLABS_API_KEY` | the famous studio "Adam" voice | free-tier key in `.env` |
| `PEXELS_API_KEY` / `PIXABAY_API_KEY` | real stock footage as a backup visual | free keys |
| `pip install crewai langgraph` | a multi-agent (Scout/Scribe/Critic) writing mode + LangGraph orchestration | heavier; not worth it on a small GPU |
| Odysseus / Wan2GP | self-hosted model gateway / local AI video clips | see `scripts/` |

> **Design principle — degrade, never die.** The core depends only on Python +
> numpy + Pillow + FFmpeg + Ollama. Every heavier piece is optional and guarded:
> if it's missing, StoryForge falls back instead of failing. That's why a missing
> CrewAI or LangGraph simply means the single-model path is used.

---

## 🔌 Optional add-ons — connect only what you want

All optional; the app is fully free and works without any of these. To enable one:
open `.env`, add the line, save, then restart the bot:
```
python scripts/install_autostart.py --restart
```

### 🎙️ ElevenLabs — the famous studio narrator voice (recommended)
1. Free account at **https://elevenlabs.io** → click your avatar (top-right) →
   **API Keys** → **Create API Key** → copy it.
2. Add to `.env`:
   ```
   ELEVENLABS_API_KEY=paste-key-here
   ```
Free tier ≈ 10,000 characters/month (~5–6 videos); it falls back to the free local
voice automatically when the quota runs out.

### 🧠 Gemini — stronger, faster story writing (optional)
1. Free key at **https://aistudio.google.com/app/apikey** → **Create API key** → copy.
2. Add to `.env`:
   ```
   GEMINI_API_KEY=paste-key-here
   ```
Used automatically whenever local Ollama isn't running.

> That's all you need. A few advanced extras (stock-footage keys, self-hosted
> models, YouTube auto-upload) live in `.env.example` if you ever want them — you
> can safely ignore them.

---

## Everyday use

Once installed and the bot is online, in your Discord channel:

```
!here                                  ← one time: talk without @mentioning
make me a short                        ← it asks Adam or squeaky, then renders
make a short about my uncle's will      ← give it your own subject
make a video "My wife hid a second phone for a year"   ← a quote becomes the opening line
```

The finished video posts itself back in ~8–15 minutes. Then:

| Command | Does |
|---|---|
| `!approve 1` | keep it → `output/<channel>/approved/` |
| `!reject 1` | delete it |
| `!pending` | list videos waiting for review |
| `!review 1` | re-watch #1 + read its full script |
| `!status` | what the pipeline is doing right now |
| `!brain` | which AI model is being used |
| `!feed` | recent activity + any errors |
| `!help` | everything |

Plain English works too. There is **no scheduling and no "night/day"** to manage —
you simply ask for a video whenever you want one.

---

## The channels

`config/*.yaml` defines each channel. Two ship by default:

- **`gossip_stories`** — modern real-life drama (the main one). Enforced
  Reddit-confession doctrine.
- **`gaming_lore`** — cinematic game-lore storytelling (`story_mode: lore`,
  exempt from the drama doctrine).

Edit a channel's YAML to change voice, length window, number of scenes, and the
banned-subjects list. Every setting is documented in the per-OS guides.

---

## Bot showing offline? One command tells you why

```bash
python scripts/install_autostart.py --doctor
```
It checks Python, discord.py, ffmpeg, your token, Ollama, your clips, whether the
bot is running, and prints the real error from the log. Then:
```bash
python scripts/install_autostart.py --restart
```
Full offline troubleshooting is in the per-OS guides.

---

## Compliance

Human approval on every video is **required and permanent** — YouTube's 2025–2026
policy demonetises mass-produced content with no meaningful human involvement, and
that review is the safeguard. Descriptions carry the AI-content disclosure, stories
are fiction (never presented as real), and prompts exclude real people and brands.
See [COMPLIANCE.md](COMPLIANCE.md).
