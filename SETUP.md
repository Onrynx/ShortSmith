# StoryForge — Setup Guide (macOS & Windows)

Everything runs **on your own laptop** and is **free**. You control it by chatting
with your own Discord bot. Nothing is shared with anyone else: your bot, your
videos, your keys all stay on your machine.

> ⏱️ First-time setup: ~20 minutes, most of it downloads.

---

## 0) What you're installing (and why)

| Thing | Why | Cost |
|---|---|---|
| Python 3.11+ | runs StoryForge | free |
| FFmpeg | cuts/renders the video | free |
| Ollama + a model | writes the stories on your laptop | free |
| A Discord bot | your remote control | free |
| Gameplay clips | the parkour background | free (you download) |

---

## 1) macOS install

Open **Terminal** (⌘+Space → "Terminal") and paste these one at a time.

```bash
# Homebrew (skip if you have it) — the Mac package installer
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# The tools
brew install python git ffmpeg
brew install --cask ollama
```

Open the **Ollama** app once (Applications → Ollama) so it starts running, then:

```bash
ollama pull qwen2.5:14b     # the story writer (~9GB, one time)
ollama pull qwen3:8b        # the chat brain (~5GB)
```

> 💡 If your Mac has less than 16GB RAM, use `ollama pull llama3.2:3b` instead and
> put `OLLAMA_MODEL=llama3.2:3b` in your `.env` (step 4).

### Windows install
Install [Python](https://python.org) (tick **Add to PATH**), [Git](https://git-scm.com),
[Ollama](https://ollama.com), then in PowerShell: `winget install Gyan.FFmpeg`
and the same two `ollama pull` commands.

---

## 2) Get StoryForge

```bash
cd ~/Desktop
git clone <PASTE-THE-GITHUB-LINK-HERE> "Youtube Ecosystem"
cd "Youtube Ecosystem"
pip3 install -r requirements.txt
pip3 install discord.py faster-whisper kokoro-onnx onnxruntime
```

---

## 3) Create YOUR OWN Discord bot

**Important:** bots can't be shared — you make your own. It takes 3 minutes.
(Your friend's bot and yours are completely separate programs.)

1. Go to **discord.com/developers/applications** → **New Application** → name it
   anything (e.g. "Valentine") → Create.
2. Left sidebar → **Installation** → set **Install Link** to **None** → Save.
   *(Skipping this causes the "Private application cannot have a default
   authorization link" error.)*
3. Left sidebar → **Bot**:
   - **Public Bot** → **OFF**
   - Scroll to *Privileged Gateway Intents* → **MESSAGE CONTENT INTENT** → **ON**
     *(without this the bot cannot read your messages — it will look broken)*
   - **Reset Token** → confirm (it asks for your 2FA code / password) → **Copy** it.
     You only see it once.
   - Save Changes.
4. Left sidebar → **OAuth2** → **URL Generator**:
   - Scopes: tick **bot**
   - Bot Permissions: **View Channels, Send Messages, Attach Files, Read Message History**
   - Copy the **Generated URL** at the bottom → open it in your browser → pick your
     server → **Authorize**.

> These 4 permissions are all it needs. Everything heavy (writing, rendering,
> approving) happens on your laptop — Discord is only the remote control.

Don't have a server? In Discord: **+** on the left → *Create My Own* → done.

---

## 4) Configure

```bash
cp .env.example .env
open -e .env          # macOS (Windows: notepad .env)
```

Set just this one line to start:

```
DISCORD_BOT_TOKEN=paste-the-token-from-step-3
```

Optional extras (all safe to leave blank):
```
ELEVENLABS_API_KEY=      # famous AI narrator voice, free tier ~10k chars/month
DISCORD_ADMIN_IDS=       # your Discord user id, so only you can run/approve
OLLAMA_MODEL=qwen2.5:14b # smaller model if your laptop is light on RAM
CAPTION_FONT=Arial Black # change if captions look wrong on your system
```

---

## 5) Add your background clips + music

These are **not** in the download (they're big and personal). Add your own:

- **`assets/backgrounds/`** → 3–5 gameplay clips (`.mp4`). Search YouTube for
  *"no copyright minecraft parkour free to use"* and download ones whose creator
  allows reuse — or record your own gameplay (safest). 5+ minutes each is ideal.
  StoryForge picks a random clip and a random section every single video.
- **`assets/music/`** → optional. Drop tracks into `mystery/`, `epic/`, `dark/`,
  `adventure/`, `chill/` and it picks one matching each story's mood. Get free
  tracks from the YouTube Audio Library or NCS. If empty, it generates its own
  soft background music automatically.

---

## 6) Run it

```bash
python3 -m storyforge.discord_bot      # macOS/Linux
# or: ./scripts/launch_all.sh
```
Windows: `python -m storyforge.discord_bot`

The bot goes **online** in your server. In your channel type:

```
!here                    ← makes this channel its home (no @ needed afterwards)
make me a gossip short   ← just talk to it normally
```

Wait ~5–10 minutes (the story model is thinking). The finished vertical video
**posts itself into the channel**. Then:

```
!approve 1     keep it  (moves to output/gossip_stories/approved/)
!reject 1      bin it
```

Upload approved videos to YouTube yourself — StoryForge never touches your
YouTube account unless you deliberately set that up later.

### Commands
`!help` `!status` `!brain` `!pending` `!review 1` `!approve 1` `!reject 1`
`!run gossip_stories night` `!daily` `!lanes <night> <day>` `!digest` `!feed`
…or just chat in plain English.

---

## 7) Troubleshooting

| Problem | Fix |
|---|---|
| Bot online but ignores you | MESSAGE CONTENT INTENT is off — step 3.3, then restart the bot |
| "Private application cannot have a default authorization link" | Installation → Install Link → **None** |
| Stories are the same canned one every time | Ollama isn't running — open the Ollama app; check `!brain` |
| Takes 5–10 min per video | Normal. The 14B model writes on your CPU. Use a smaller `OLLAMA_MODEL` to speed it up |
| `ffmpeg: command not found` | `brew install ffmpeg` |
| Video is abstract colours, not parkour | `assets/backgrounds/` is empty — step 5 |
| Robotic voice | Normal free fallback (Kokoro). Add `ELEVENLABS_API_KEY` for the famous voice |
| Video too big to post in Discord | It auto-shrinks a preview. Full quality is always in `output/<channel>/approved/` |

---

## Where your videos live
```
output/<channel>/pending/    waiting for you to approve
output/<channel>/approved/   ready to upload to YouTube
```
Each video ships with a `.report.txt` (full script + compliance checklist) and a
thumbnail. Nothing is ever uploaded anywhere without you doing it.
