# StoryForge — Complete macOS Guide

Everything you need, start to finish, assuming **zero coding experience**.
Every command is copy-paste. Nothing here costs money.

---

## Table of contents
1. [What this actually is](#1-what-this-actually-is)
2. [Will my Mac run it?](#2-will-my-mac-run-it)
3. [Open Terminal (never used it? read this)](#3-open-terminal)
4. [Install Homebrew](#4-install-homebrew)
5. [Install the tools](#5-install-the-tools)
6. [Install the AI brain](#6-install-the-ai-brain-ollama)
7. [Download StoryForge](#7-download-storyforge)
8. [Install Python packages](#8-install-python-packages)
9. [Create YOUR Discord bot](#9-create-your-discord-bot)
10. [Configure the .env file](#10-configure-the-env-file)
11. [Add gameplay clips + music](#11-add-gameplay-clips--music)
12. [Turn it on](#12-turn-it-on)
13. [Daily use](#13-daily-use)
14. [Uploading to YouTube](#14-uploading-to-youtube)
15. [Every setting explained](#15-every-setting-explained)
16. [Troubleshooting](#16-troubleshooting)
17. [Updating / uninstalling](#17-updating--uninstalling)

---

## 1. What this actually is

A **fully local, free** YouTube Shorts factory. You chat with your own Discord
bot; it writes an original story, narrates it in an AI voice, lays it over
Minecraft-parkour gameplay with animated captions, and posts the finished
vertical video back to you for approval.

**One video =** original AI story (2.5–2.9 min) → AI narration → gameplay
background → word-by-word captions → mood-matched music → thumbnail → your
review in Discord.

**Nothing is uploaded anywhere.** Videos stay on your Mac until *you* upload them.

---

## 2. Will my Mac run it?

| | Minimum | Recommended |
|---|---|---|
| macOS | Big Sur (11) | Sonoma / Sequoia |
| Chip | Intel or Apple Silicon | **Apple Silicon (M1–M4)** — much faster |
| RAM | 8 GB | 16 GB+ |
| Free disk | 25 GB | 40 GB+ |

Apple Silicon runs the AI on the GPU automatically — an M-series Mac is
noticeably faster than most Windows laptops here. **8 GB RAM?** Use the small
model in [step 6](#6-install-the-ai-brain-ollama).

**Check yours:**  → About This Mac (shows Chip and Memory).

---

## 3. Open Terminal

Press **⌘ + Space**, type `Terminal`, press **Enter**. A text window opens —
this is where every command below goes. Paste with **⌘ + V**, run with **Enter**.

Two things that surprise first-timers:
- When it asks for your **password, nothing appears as you type.** That's normal
  — type it and press Enter.
- Some commands look frozen for several minutes. They're downloading. **Don't
  close the window.**

---

## 4. Install Homebrew

Homebrew installs everything else. Paste this **one line**:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

It asks for your Mac password, then press **Enter** to confirm. Takes ~5 minutes.

### ⚠️ Apple Silicon (M1/M2/M3/M4) — REQUIRED extra step
Homebrew isn't on your PATH by default. Nothing will work without this:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Verify: `brew --version` must print a version number.

---

## 5. Install the tools

```bash
brew install python git ffmpeg
```

Verify all three:
```bash
python3 --version
git --version
ffmpeg -version
```

Each must print a version.

> On macOS always type **`python3`** and **`pip3`** — plain `python` is an old
> Apple version that won't work.

---

## 6. Install the AI brain (Ollama)

This writes the stories — on your Mac, free, forever.

```bash
brew install --cask ollama
```

Open **Finder → Applications → Ollama** and double-click it once. A llama icon
appears in the menu bar (top-right). **It must be running** for stories to work.

Then download a model:

```bash
ollama pull qwen2.5:14b
ollama pull qwen3:8b
```

| Your RAM | Model | Command |
|---|---|---|
| 16 GB+ | qwen2.5:14b (best writing) | as above |
| 8 GB | llama3.2:3b (fast, simpler) | `ollama pull llama3.2:3b` |

Using the small one? Add `OLLAMA_MODEL=llama3.2:3b` to `.env` in
[step 10](#10-configure-the-env-file).

> **Disk use:** qwen2.5:14b is 9 GB, qwen3:8b is 5 GB. One-time download.

---

## 7. Download StoryForge

```bash
cd ~/Desktop
git clone <PASTE-THE-GITHUB-LINK-HERE> "Youtube Ecosystem"
cd "Youtube Ecosystem"
```

macOS may ask to install "command line developer tools" the first time you use
`git` — click **Install** and wait, then re-run the command.

---

## 8. Install Python packages

```bash
pip3 install -r requirements.txt
pip3 install discord.py faster-whisper kokoro-onnx onnxruntime
```

> **"error: externally-managed-environment"?** Newer macOS Python blocks global
> installs. Safe fix here:
> ```bash
> pip3 install --break-system-packages -r requirements.txt
> pip3 install --break-system-packages discord.py faster-whisper kokoro-onnx onnxruntime
> ```

What each is for:

| Package | Purpose | Required? |
|---|---|---|
| `discord.py` | the Discord bot | **yes** |
| `kokoro-onnx`, `onnxruntime` | the free AI narrator voice | **yes** |
| `faster-whisper` | perfectly synced word-by-word captions | strongly recommended |
| `numpy`, `Pillow` | video frames + thumbnails | **yes** |
| `PyYAML` | reads the channel config files | yes |
| `crewai`, `langgraph` | optional multi-agent writing mode | optional |

Everything has a fallback: a missing optional package degrades gracefully rather
than breaking the run.

---

## 9. Create YOUR Discord bot

**A bot cannot be shared** — the token is a password. Everyone runs their own.
Takes ~3 minutes.

1. Go to **https://discord.com/developers/applications** → **New Application** →
   name it (e.g. `Valentine`) → **Create**.
2. Left sidebar → **Installation** → set **Install Link** to **None** → **Save Changes**.
   > Skipping this causes *"Private application cannot have a default
   > authorization link."*
3. Left sidebar → **Bot**:
   - **Public Bot** → **OFF**
   - Scroll to *Privileged Gateway Intents* → **MESSAGE CONTENT INTENT** → **ON**
     > ⚠️ **The single most-missed step.** Without it the bot looks online but
     > ignores every message.
   - **Reset Token** → confirm (asks for your 2FA code or password) → **Copy**.
     **You only see it once.**
   - **Save Changes**
4. Left sidebar → **OAuth2** → **URL Generator**:
   - **Scopes:** tick `bot`
   - **Bot Permissions:** `View Channels`, `Send Messages`, `Attach Files`,
     `Read Message History`
   - Copy the **Generated URL** at the bottom → open in your browser → choose
     your server → **Authorize**.

> **Why so few permissions?** Discord is only a remote control. All the real work
> happens on your Mac, which needs no Discord permission. If the token leaked,
> the damage would be limited to posting messages.

**No server?** In Discord: **+** on the left sidebar → *Create My Own* → Skip.

---

## 10. Configure the .env file

```bash
cp .env.example .env
open -e .env
```

TextEdit opens. Only **one line** is required — paste your token from step 9:

```
DISCORD_BOT_TOKEN=your-token-here
```

Save with **⌘ + S**, close. Everything else is optional — see
[section 15](#15-every-setting-explained).

> `.env` holds your passwords. It is gitignored and never uploaded anywhere.

---

## 11. Add gameplay clips + music

These are **not** included (too large, and licensing is personal to you).

### Backgrounds — required
Put **3–5 gameplay videos** in `assets/backgrounds/`:

```bash
open assets/backgrounds
```
A Finder window opens — drag your `.mp4` files in.

- Search YouTube for **"no copyright minecraft parkour free to use"** and
  download clips whose creator explicitly permits reuse, **or record your own
  gameplay** (safest — zero copyright questions).
- 5+ minutes each is ideal. Landscape is fine — StoryForge auto-crops to
  vertical 9:16.
- It picks a **random clip and random section** every video, so uploads never
  look identical.

**Empty folder?** Videos still render, but with abstract animated colours.

### Music — optional
Drop tracks into `assets/music/<mood>/` — folders: `mystery`, `epic`, `dark`,
`adventure`, `chill`. StoryForge picks one matching the story's mood.
Free sources: YouTube Audio Library (studio.youtube.com), NCS, Pixabay Music.
**If empty, it generates its own soft background music automatically.**

---

## 12. Turn it on

```bash
python3 scripts/install_autostart.py
```

That's it. The bot now starts **silently every time you log in** — no Terminal
window ever appears, and it uses ~50 MB RAM while idle.

```bash
python3 scripts/install_autostart.py --status      # is it running?
python3 scripts/install_autostart.py --uninstall   # stop it starting at login
```

> **How it works on Mac:** it installs a **LaunchAgent** (Apple's official system
> for background apps) at `~/Library/LaunchAgents/com.storyforge.bot.plist`. It
> also restarts itself automatically if it ever crashes.

**Keep the Ollama app running** (llama icon in the menu bar) — that's the AI.

---

## 13. Daily use

In your Discord channel, **once ever**:

```
!here
```

This makes that channel the bot's home so you can talk normally without `@`.

Then just chat:

```
make me a short
```

The bot asks **Adam** (deep narrator) or **squeaky** (high-pitched chipmunk).
Reply with one, and ~8–15 minutes later the finished video **posts itself into
the channel**.

**Give it your own story idea:**
```
make a short about my brother-in-law forging my dad's signature
make a video "My wife's best friend moved into our house and never left"
```
A quoted sentence becomes the literal opening line of the story.

### All commands

| Command | What it does |
|---|---|
| `!help` | list commands |
| `!status` | what each lane is doing right now |
| `!brain` | which AI model is being used |
| `!pending` | videos waiting for your review |
| `!review 1` | watch #1 + read its full script |
| `!approve 1` | keep it → moves to `approved/` |
| `!reject 1` | delete it |
| `!run gossip_stories` | start a render directly (same as chatting) |
| `!feed` | recent activity + any errors |
| `!digest` | today's summary |
| `!here` / `!leave` | set / unset the home channel |

Plain English works too — "what's pending?", "approve it", "how's it going?"

---

## 14. Uploading to YouTube

Approved videos live in:
```
output/gossip_stories/approved/
```
Open it quickly: `open output/gossip_stories/approved`

Each has the `.mp4`, a `.jpg` thumbnail, and a `.report.txt` with the full script
and a compliance checklist.

**Upload manually** (recommended): drag the mp4 into YouTube Studio and use the
title/description from the report — it already contains the required AI
disclosure and hashtags.

**Auto-upload** (optional, always PRIVATE):
```bash
pip3 install -r requirements-cloud.txt
```
Then set `PUBLISH=true` plus `YOUTUBE_CLIENT_SECRETS` and `YOUTUBE_TOKEN_JSON`
in `.env`. It uploads as **private** with the synthetic-content disclosure set —
you still flip it public yourself.

> **Why the human approval step exists:** YouTube's 2025–26 policy demonetises
> mass-produced content with no meaningful human involvement. Reviewing every
> video before it goes out is the protection. Don't remove it.

---

## 15. Every setting explained

### `.env`

| Setting | Default | What it does |
|---|---|---|
| `DISCORD_BOT_TOKEN` | — | **Required.** Your bot's password |
| `DISCORD_ADMIN_IDS` | empty | Comma-separated Discord user IDs allowed to render/approve. Empty = everyone in the server |
| `DISCORD_CHANNEL_ID` | `0` | Restrict commands to one channel. `0` = any |
| `OLLAMA_MODEL` | auto | Force a model, e.g. `llama3.2:3b` |
| `OLLAMA_CHAT_MODEL` | `qwen3:8b` | Faster model for chat replies |
| `OLLAMA_NUM_CTX` | `16384` | Context size. Lower to `8192` if low on RAM |
| `GEMINI_API_KEY` | empty | Free Google key → much better + faster stories |
| `ELEVENLABS_API_KEY` | empty | Free tier → the famous "Adam" narrator voice |
| `VOICE_ENGINE` | `auto` | `elevenlabs` \| `tiktok` \| `kokoro` |
| `VOICE_SPEED` | `1.18` | Narration pace. 1.18 ≈ 177 wpm (viral range is 160–180) |
| `CAPTION_FONT` | `Arial Black` | Caption typeface. Try `Helvetica` if captions look wrong |
| `PEXELS_API_KEY` / `PIXABAY_API_KEY` | empty | Free stock footage as a backup visual |
| `AUTO_PUBLISH` | `off` | `off` \| `private` \| `public` |

### `config/gossip_stories.yaml`

| Setting | Meaning |
|---|---|
| `voice` | Kokoro voice (`am_adam` = deep male) |
| `voice_speed` | base pace multiplier |
| `min_seconds` / `target_seconds` | video length window (150–175 s) — a random length each run |
| `max_seconds` | hard ceiling (178 s; Shorts must stay under 3 min) |
| `scene_bounds` | how many scenes (16–24) |
| `story_mode` | `real_life_drama` enforces the Reddit-confession doctrine |
| `banned` | subjects the writer must avoid |

---

## 🔴 Bot won't turn on / shows offline in Discord — full fix

**Step 1 — always run the doctor first.** It checks every requirement and prints
the real reason from the log:
```
python3 scripts/install_autostart.py --doctor     # Mac/Linux
python  scripts\install_autostart.py --doctor     # Windows
```
**Step 2 — restart it:**
```
python3 scripts/install_autostart.py --restart
```

Want to watch the error happen live? Run the bot in the foreground — it prints
the crash reason straight to the terminal:
```
python3 -m storyforge.discord_bot                 # Mac/Linux  (python on Windows)
```
Ctrl+C to stop, then go back to background with `--restart`.

### The exact causes, most common first

1. **The computer was off, asleep, or logged out.** The bot runs only while the
   machine is on and you are logged in — it is NOT a cloud service. Discord shows
   it offline whenever the laptop is off. Wake / log in → it reconnects in ~10s.
2. **No internet / flaky Wi-Fi.** If the log says `Cannot connect to host …
   discord.gg` or `getaddrinfo failed`, it's your connection. It auto-reconnects.
3. **Bad or reset token** (`LoginFailure` / `Improper token` in the log). If you
   ever clicked *Reset Token*, the old one is dead. Paste the new one into `.env`
   after `DISCORD_BOT_TOKEN=` → `--restart`.
4. **discord.py not installed** (`ModuleNotFoundError: 'discord'`). Install it with
   the SAME Python the doctor prints: `pip install discord.py` (add
   `--break-system-packages` on Mac if needed).
5. **Autostart never installed, or you moved the folder.** Re-run
   `python3 scripts/install_autostart.py` from the folder's current location.
6. **Just started — give it 30s** to connect, then check `--status`.
7. **(Windows) it crashed and didn't restart.** Windows starts it at login but
   won't auto-restart on crash → run `--restart` (Mac auto-restarts itself).

### It's ONLINE but ignores every message
Different problem: **MESSAGE CONTENT INTENT is OFF.** Developer Portal → your app →
**Bot** → turn **MESSAGE CONTENT INTENT** on → Save → `--restart`.

### Read the log yourself (the last line is the reason 99% of the time)
```
Mac/Linux:  tail -40 .storyforge_state/bot.log
Windows:    Get-Content .storyforge_stateot.log -Tail 40
```

---

## ⏻ Turning it off & back on

**Temporarily switch it off (pause for now):**
```
python3 scripts/install_autostart.py --stop
```
Stops the bot right now. It comes back on its own the next time you turn the
computer on — or bring it back immediately with `python3 scripts/install_autostart.py --restart`.

**Switch it off permanently (never runs at startup again):**
```
python3 scripts/install_autostart.py --uninstall
```
Removes it from startup so it will never launch by itself again. Your videos,
settings, `.env` and downloaded models are all left untouched. (To also free up
disk space later: `ollama rm qwen2.5:14b` and delete the "Youtube Ecosystem" folder.)

> **▶️ To start it up again after uninstalling:** just re-run the "Turn it on" step —
> `python3 scripts/install_autostart.py`. Everything else is already installed, so you
> do **not** repeat any of the earlier setup steps.

---

## 16. Troubleshooting

| Problem | Cause & fix |
|---|---|
| `command not found: brew` | Apple Silicon PATH step missing — [step 4](#4-install-homebrew) |
| `command not found: python3` | `brew install python`, then open a **new** Terminal |
| "externally-managed-environment" | Add `--break-system-packages` — [step 8](#8-install-python-packages) |
| Bot never appears online | `python3 scripts/install_autostart.py --status`, then check `.storyforge_state/bot.log`. Usually a bad token |
| Bot online but ignores messages | **MESSAGE CONTENT INTENT is off** — step 9.3, then re-run the installer |
| "Private application cannot have a default authorization link" | Installation → Install Link → **None** |
| Same story every time | Ollama isn't running — open the app; verify with `!brain`. If it says `mock`, that's the offline canned story |
| Videos ~45 s instead of 2.5 min | Old version — `git pull` |
| Abstract colours instead of gameplay | `assets/backgrounds/` is empty — step 11 |
| Captions look wrong / boxes | Add `CAPTION_FONT=Helvetica` to `.env` |
| Takes 10+ min per video | Normal on Intel Macs. Use a smaller `OLLAMA_MODEL`, or add `GEMINI_API_KEY` |
| Video won't post in Discord | Over the upload limit — it auto-shrinks a preview. Full quality is in `output/` |
| Bot stops when Mac sleeps | Expected. It resumes on wake; renders continue |

**Log files** — `.storyforge_state/bot.log` (the bot) and
`.storyforge_state/runs/*.log` (each video).

---

## 17. Updating / uninstalling

```bash
cd ~/Desktop/"Youtube Ecosystem"
git pull
pip3 install -r requirements.txt
python3 scripts/install_autostart.py     # re-register after an update
```

Your `.env`, clips, music and videos are never touched by an update.

**Uninstall:**
```bash
python3 scripts/install_autostart.py --uninstall
```
Then drag the folder to the Trash. Remove models with `ollama rm qwen2.5:14b`,
and uninstall the tools with `brew uninstall --cask ollama` /
`brew uninstall ffmpeg python git`.

---

## Where everything lives

```
Youtube Ecosystem/
├── config/*.yaml              channel settings (length, voice, rules)
├── assets/backgrounds/        YOUR gameplay clips
├── assets/music/<mood>/       YOUR music (optional)
├── output/<channel>/pending/  finished, awaiting your review
├── output/<channel>/approved/ approved — ready to upload
├── storyforge/                the program
├── scripts/install_autostart.py
├── .env                       your keys (never shared)
└── .storyforge_state/         logs, history, bot state
```
