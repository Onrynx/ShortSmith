# StoryForge — Complete Windows Guide

Everything you need, start to finish, assuming **zero coding experience**.
Every command is copy-paste. Nothing here costs money.

---

## Table of contents
1. [What this actually is](#1-what-this-actually-is)
2. [Will my PC run it?](#2-will-my-pc-run-it)
3. [Install the tools](#3-install-the-tools)
4. [Install the AI brain](#4-install-the-ai-brain-ollama)
5. [Download StoryForge](#5-download-storyforge)
6. [Install Python packages](#6-install-python-packages)
7. [Create YOUR Discord bot](#7-create-your-discord-bot)
8. [Configure the .env file](#8-configure-the-env-file)
9. [Add gameplay clips + music](#9-add-gameplay-clips--music)
10. [Turn it on](#10-turn-it-on)
11. [Daily use](#11-daily-use)
12. [Uploading to YouTube](#12-uploading-to-youtube)
13. [Every setting explained](#13-every-setting-explained)
14. [Troubleshooting](#14-troubleshooting)
15. [Updating / uninstalling](#15-updating--uninstalling)

---

## 1. What this actually is

A **fully local, free** YouTube Shorts factory. You chat with your own Discord
bot; it writes an original story, narrates it in an AI voice, lays it over
Minecraft-parkour gameplay with animated captions, and posts the finished
vertical video back to you for approval.

**One video =** original AI story (2.5–2.9 min) → AI narration → gameplay
background → word-by-word captions → mood-matched music → thumbnail → your
review in Discord.

**Nothing is uploaded anywhere.** Videos sit on your PC until *you* upload them.

---

## 2. Will my PC run it?

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 64-bit | Windows 11 |
| RAM | 8 GB | 16 GB+ |
| Free disk | 25 GB | 40 GB+ |
| GPU | none (CPU works) | any NVIDIA (faster) |

**8 GB RAM?** Use the small model — see [step 4](#4-install-the-ai-brain-ollama).
A video takes ~8–15 min depending on your CPU. That's normal; it runs in the
background and you carry on working.

---

## 3. Install the tools

Open **PowerShell** (Start → type `powershell` → Enter) and run these one at a
time. Say **Yes** to any admin prompt.

```powershell
winget install Python.Python.3.12
winget install Git.Git
winget install Gyan.FFmpeg
```

**Now close PowerShell and open a NEW one** (so it picks up the new programs).
Verify all three:

```powershell
python --version
git --version
ffmpeg -version
```

Each must print a version. If `python` opens the Microsoft Store instead:
Start → "Manage app execution aliases" → turn **OFF** both `python.exe` entries.

<details><summary>If <code>winget</code> isn't available</summary>

Download and install manually — during the Python install **tick "Add python.exe
to PATH"**:
- Python: https://www.python.org/downloads/
- Git: https://git-scm.com/download/win
- FFmpeg: https://www.gyan.dev/ffmpeg/builds/ (get `ffmpeg-release-essentials.zip`,
  extract to `C:\ffmpeg`, then add `C:\ffmpeg\bin` to your PATH via
  Start → "Edit the system environment variables" → Environment Variables → Path → New)
</details>

---

## 4. Install the AI brain (Ollama)

This is what writes the stories — on your machine, free, forever.

```powershell
winget install Ollama.Ollama
```

Open the **Ollama** app once from the Start menu. A llama icon appears in your
system tray (bottom-right). **It must be running** for stories to be written.

Then download a model:

```powershell
ollama pull qwen2.5:14b
ollama pull qwen3:8b
```

| Your RAM | Use this instead | Command |
|---|---|---|
| 16 GB+ | qwen2.5:14b (best writing) | as above |
| 8–12 GB | llama3.2:3b (fast, simpler) | `ollama pull llama3.2:3b` |

If you use the small model, add `OLLAMA_MODEL=llama3.2:3b` to your `.env` in
[step 8](#8-configure-the-env-file).

> **Disk use:** qwen2.5:14b is 9 GB, qwen3:8b is 5 GB. One-time download.

---

## 5. Download StoryForge

```powershell
cd $HOME\Desktop
git clone <PASTE-THE-GITHUB-LINK-HERE> "Youtube Ecosystem"
cd "Youtube Ecosystem"
```

---

## 6. Install Python packages

```powershell
pip install -r requirements.txt
pip install discord.py faster-whisper kokoro-onnx onnxruntime
```

What each is for:

| Package | Purpose | Required? |
|---|---|---|
| `discord.py` | the Discord bot | **yes** |
| `kokoro-onnx`, `onnxruntime` | the free AI narrator voice | **yes** |
| `faster-whisper` | perfectly synced word-by-word captions | strongly recommended |
| `numpy`, `Pillow` | video frames + thumbnails | **yes** |
| `PyYAML` | reads the channel config files | yes |
| `crewai`, `langgraph` | optional multi-agent writing mode | optional |

Everything has a fallback: if an optional package is missing, StoryForge
degrades instead of failing.

---

## 7. Create YOUR Discord bot

**A bot cannot be shared** — the token is a password. Everyone runs their own.
Takes ~3 minutes.

1. Go to **https://discord.com/developers/applications** → **New Application** →
   name it (e.g. `Valentine`) → **Create**.
2. Left sidebar → **Installation** → set **Install Link** to **None** → **Save Changes**.
   > Skipping this causes the error *"Private application cannot have a default
   > authorization link."*
3. Left sidebar → **Bot**:
   - **Public Bot** → **OFF**
   - Scroll to *Privileged Gateway Intents* → **MESSAGE CONTENT INTENT** → **ON**
     > ⚠️ **The single most-missed step.** Without it the bot appears online but
     > ignores every message.
   - **Reset Token** → confirm (asks for your 2FA code or password) → **Copy**.
     **You only see it once.**
   - **Save Changes**
4. Left sidebar → **OAuth2** → **URL Generator**:
   - **Scopes:** tick `bot`
   - **Bot Permissions:** `View Channels`, `Send Messages`, `Attach Files`,
     `Read Message History`
   - Copy the **Generated URL** at the bottom → paste in your browser → choose
     your server → **Authorize**.

> **Why so few permissions?** Discord is only a remote control. All the real work
> (writing, rendering, approving) happens on your PC, which needs no Discord
> permission at all. If the token ever leaked, the damage would be limited to
> posting messages.

**No server?** In Discord: **+** on the left sidebar → *Create My Own* → Skip.

---

## 8. Configure the .env file

```powershell
copy .env.example .env
notepad .env
```

Only **one line** is required — paste the token from step 7:

```
DISCORD_BOT_TOKEN=your-token-here
```

Save (Ctrl+S) and close. Everything else is optional — see
[section 13](#13-every-setting-explained).

> `.env` holds your passwords. It is gitignored and never uploaded anywhere.

---

## 9. Add gameplay clips + music

These are **not** included (too large, and licensing is personal to you).

### Backgrounds — required
Put **3–5 gameplay videos** in `assets\backgrounds\`:

```powershell
explorer assets\backgrounds
```

- Search YouTube for **"no copyright minecraft parkour free to use"** and
  download clips whose creator explicitly permits reuse, **or record your own
  gameplay** (safest — zero copyright questions).
- 5+ minutes each is ideal. Any resolution; landscape is fine — StoryForge
  auto-crops to vertical 9:16.
- It picks a **random clip and a random section** every single video, so your
  uploads never look identical.

**Empty folder?** Videos still render, but with abstract animated colours instead
of gameplay.

### Music — optional
Drop tracks into `assets\music\<mood>\` — folders: `mystery`, `epic`, `dark`,
`adventure`, `chill`. StoryForge picks one matching each story's mood.
Free sources: YouTube Audio Library (studio.youtube.com), NCS, Pixabay Music.
**If empty, it generates its own soft background music automatically.**

---

## 10. Turn it on

```powershell
python scripts\install_autostart.py
```

That's it. The bot now starts **silently every time you log in** — no window
ever appears, and it uses ~50 MB RAM while idle.

```powershell
python scripts\install_autostart.py --status      # is it running?
python scripts\install_autostart.py --uninstall   # stop it starting at login
```

> **How it stays invisible:** it runs under `pythonw.exe`, a version of Python
> with no console window, launched from your Startup folder.

---

## 11. Daily use

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

## 12. Uploading to YouTube

Approved videos live in:
```
output\gossip_stories\approved\
```
Each has the `.mp4`, a `.jpg` thumbnail, and a `.report.txt` with the full script
and a compliance checklist.

**Upload manually** (recommended): drag the mp4 into YouTube Studio, use the
title/description from the report — it already contains the required AI
disclosure and hashtags.

**Auto-upload** (optional, always PRIVATE):
```powershell
pip install -r requirements-cloud.txt
```
Then set `PUBLISH=true` plus `YOUTUBE_CLIENT_SECRETS` and `YOUTUBE_TOKEN_JSON`
in `.env`. It uploads as **private** with the synthetic-content disclosure set —
you still flip it public yourself.

> **Why the human approval step exists:** YouTube's 2025–26 policy demonetises
> mass-produced content with no meaningful human involvement. Reviewing every
> video before it goes out is the protection. Don't remove it.

---

## 13. Every setting explained

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
| `CAPTION_FONT` | `Arial Black` | Caption typeface |
| `PEXELS_API_KEY` / `PIXABAY_API_KEY` | empty | Free stock footage as a backup visual |
| `AUTO_PUBLISH` | `off` | `off` \| `private` \| `public` |

### `config\gossip_stories.yaml`

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

## 14. Troubleshooting

| Problem | Cause & fix |
|---|---|
| Bot never appears online | `python scripts\install_autostart.py --status`. If not running, check `.storyforge_state\bot.log`. Usually a bad/expired token |
| Bot is online but ignores messages | **MESSAGE CONTENT INTENT is off** — step 7.3. Then re-run the installer |
| "Private application cannot have a default authorization link" | Installation → Install Link → **None** |
| Same story every time | Ollama isn't running (open the app) — check with `!brain`. If it says `mock`, that's the offline canned story |
| Videos are ~45 s instead of 2.5 min | You're on an old version — `git pull` |
| Abstract colours instead of gameplay | `assets\backgrounds\` is empty — step 9 |
| `ffmpeg is not recognized` | FFmpeg not on PATH — reopen PowerShell, or reinstall via step 3 |
| `pip is not recognized` | Use `python -m pip install ...` |
| Takes 10+ min per video | Normal on CPU. Use a smaller `OLLAMA_MODEL`, or add `GEMINI_API_KEY` |
| Video won't post in Discord | Over the upload limit — it auto-shrinks a preview. Full quality is always in `output\` |
| Console windows flashing | Old version — `git pull` (fixed with `CREATE_NO_WINDOW`) |

**Log files** — `.storyforge_state\bot.log` (the bot) and
`.storyforge_state\runs\*.log` (each video).

---

## 15. Updating / uninstalling

```powershell
cd $HOME\Desktop\"Youtube Ecosystem"
git pull
pip install -r requirements.txt
python scripts\install_autostart.py     # re-register after an update
```

Your `.env`, clips, music and videos are never touched by an update.

**Uninstall:**
```powershell
python scripts\install_autostart.py --uninstall
```
Then delete the folder. Remove models with `ollama rm qwen2.5:14b`, and
uninstall Ollama/Python/FFmpeg from Windows Settings → Apps.

---

## Where everything lives

```
Youtube Ecosystem\
├── config\*.yaml              channel settings (length, voice, rules)
├── assets\backgrounds\        YOUR gameplay clips
├── assets\music\<mood>\       YOUR music (optional)
├── output\<channel>\pending\  finished, awaiting your review
├── output\<channel>\approved\ approved — ready to upload
├── storyforge\                the program
├── scripts\install_autostart.py
├── .env                       your keys (never shared)
└── .storyforge_state\         logs, history, bot state
```
