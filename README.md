# Shortsmith V2

Make YouTube Shorts on your own laptop. You type an idea — or nothing at all —
and a few minutes later a finished vertical video is waiting for you to approve:
original story, AI narration, gameplay background, word-by-word captions.

**Everything runs on your computer.** No account, no subscription, no API keys,
no cloud. Nothing you make is uploaded anywhere until you decide to post it.

---

## Get it

### Step 1 — Download Shortsmith

Click the green **Code** button at the top of this page → **Download ZIP**.
Unzip it anywhere you like (your Desktop is fine).

### Step 2 — Install Python (once)

Shortsmith is written in Python, so your computer needs it.

- **Windows** — [python.org/downloads](https://www.python.org/downloads/) → the
  big yellow **Download Python** button → run the installer →
  **tick "Add python.exe to PATH"** on the first screen → Install Now.
- **Mac** — [python.org/downloads](https://www.python.org/downloads/) → Download
  → open the `.pkg` → click through it.

> Ticking **Add python.exe to PATH** on Windows matters. If you miss it, re-run
> the installer and choose Modify.

### Step 3 — Install Ollama (once)

This is the AI that writes your stories, running locally.

Go to [ollama.com/download](https://ollama.com/download), get the installer for
your computer, run it. On Mac, drag it to Applications and open it once.

### Step 4 — Run the setup file

Open the Shortsmith folder and double-click:

- **Windows** → `setup.bat`
- **Mac** → `setup.command`

It installs what's needed, builds the app icon, and puts a **Shortsmith**
shortcut on your Desktop. Leave it until it says it's finished.

### Step 5 — Open Shortsmith

Double-click the **Shortsmith** icon on your Desktop. A window opens — no
terminal, no commands.

Go to **Settings** the first time. It shows what your computer can handle and
recommends a writer model. Press **Download** next to the one you want.

**Only the model you pick is downloaded** — nothing else.

| Choice | Download | What you get |
|---|---|---|
| Qwen 2.5 14B | 8.4 GB | Best stories — richest plots, most reliable pacing |
| Qwen 2.5 7B | 4.7 GB | Good. Slightly flatter plots, more rewrite passes |
| Llama 3.2 3B | 2.0 GB | Light. Weaker continuity, shorter scenes |
| Qwen 2.5 1.5B | 1.0 GB | Minimum. Basic stories. Old laptops only |

Anything your machine can't run is greyed out with the reason. The download
shows a progress bar and you can keep using the app while it runs.

---

## Add gameplay clips

Your videos play over gameplay footage, and **you supply that** — shipping
footage with the app would be a copyright problem.

Put 3–5 clips in the **`assets/backgrounds`** folder inside Shortsmith.

- `.mp4`, `.mkv`, `.webm` or `.mov`, ideally 5+ minutes each
- Record your own, or download clips whose creator allows reuse
- Shortsmith picks a **random clip and a random section every time**, and crops
  it to vertical, so your videos never look identical

No clips? Videos still render, just over an abstract moving background.

---

## Using it

Open Shortsmith and click **Make**.

**Type an idea, or leave it blank.** All of these work:

```
a guy who finds something strange in his house
revolve the story around loneliness
my brother sold my car while I was away
make it about a real historical event
```

A vague idea is enough — it invents the premise, the people, the setting and
the shape of the story itself. Leave the box empty and it picks something.

Then choose:

- **Length** — 45 seconds, 90 seconds, or a full 2–3 minutes
- **Narrator** — Adam (deep) or Squeaky (high and fast)
- **How involved you want to be:**

| Mode | What happens |
|---|---|
| **Guided** | You read the story before it renders, then review the video |
| **Express** | Skips the story check — straight to rendering |
| **Full auto** | No stops; it checks its own work and files what passed |

Press **Make Video**. It takes about 12–15 minutes. You can close the window —
it keeps working.

### The other tabs

- **In progress** — what it's doing right now: the story it planned, which scene
  it's writing, and a **Stop** button that works at any point
- **Review** — finished videos. Play them, read the full script, then Approve or
  Delete. Tick several to approve or delete them in one go
- **Library** — everything you approved, with **Open folder** to upload them
- **Settings** — your computer, your model, and updates

---

## If something goes wrong

**The window doesn't open.** Double-click `Shortsmith.bat` (Windows) or
`Shortsmith.command` (Mac) in the folder instead — it opens in your browser,
which works exactly the same.

**"Ollama unreachable".** Ollama isn't running. Open it from your Start menu or
Applications; it sits quietly in the tray or menu bar.

**Videos have no gameplay behind them.** No clips in `assets/backgrounds`, or
they're in a format ffmpeg can't read. Try a normal `.mp4`.

**It's slow.** The story is written by an AI on your own machine. On a laptop
without a strong graphics card, 12–15 minutes per video is normal. Choosing a
smaller model in Settings makes it quicker.

---

## Turning it off

Close the window. Nothing runs in the background, nothing starts with your
computer, nothing phones home.

To remove it completely: delete the Shortsmith folder, delete the Desktop
shortcut, and uninstall Ollama like any other app.

---

## Your privacy

- Everything happens on your computer. There is no server and no account.
- The app window talks to `127.0.0.1` — your own machine only. Nothing on your
  Wi-Fi and nothing on the internet can reach it.
- The hardware check reads your RAM, CPU and graphics card to recommend a model.
  It is read locally and **sent nowhere**.
- The only things ever downloaded are the model you explicitly choose, and
  program updates you approve.

---

## What changed from V1

V1 was controlled through a Discord bot you had to create yourself, and set up
with a long list of terminal commands. V2 is an app.

### Setup

| V1 | V2 |
|---|---|
| ~8 terminal steps: Homebrew, git, pip, virtualenvs, PATH edits | Download, install Python + Ollama, double-click setup |
| Create a Discord application, enable intents, copy a bot token, build an invite URL | Nothing — there is no account to make |
| `ollama pull` in a terminal, guessing which model fits | Settings shows what your machine can run; press Download |
| No idea how much disk it would take | Every option shows its size before you commit |

### Using it

| V1 | V2 |
|---|---|
| Chat with a Discord bot | A real app window with an icon |
| Wait 15 minutes to find out the story was bad | **Read the story before it renders** and regenerate if you don't like it |
| Approve videos one at a time with `!approve 1` | Play them in the app; tick several and approve or delete in one go |
| No record of approved videos | A Library of everything you kept |
| "What's it doing?" answered by guesswork | Live progress: the plan it chose, the scene it's writing, the opening lines |

### The stories themselves

V1 pushed every idea through one fixed arc — hook, setup, escalation, twist, and
a question at the end — and mechanically rejected anything that didn't end in a
question mark. Every video came out the same shape and raced through its plot.

V2 plans before it writes. It develops your idea into a premise, a cast and a
setting, **chooses a structure that suits that particular story**, and decides
how many seconds each beat deserves — so a scene that needs to establish
something gets room to, instead of everything escalating at once.

- Different stories now come out genuinely different shapes
- A theme like "loneliness" shapes the whole story instead of being mentioned twice
- A continuity pass re-reads the finished draft and fixes contradictions and
  characters who appeared from nowhere
- Repeated scenes are detected and rewritten

### Length

V1 multiplied a target by a fixed words-per-minute and hoped; videos came out
roughly 27% short because the assumed speaking rate was simply wrong. If a video
overran, the narration was sped up until it fit, which made the narrator sound
rushed.

V2 **measures the real narration audio** and revises the story to fit — tightening
flabby scenes or developing thin ones, while protecting the opening, the turn and
the ending. Ask for 45 seconds and you get about 45 seconds.

### Audio

V1 mixed background music under the voice. V2 has **no music and no background
noise at all** — the only sound is the narrator.

### Running it

| V1 | V2 |
|---|---|
| A background process that started with your computer | Runs only while the window is open |
| Night/day "lanes" and a daily schedule to manage | Gone. It makes a video when you ask |
| Fantasy/game-lore channel could be picked by accident | Real-life drama by default; other styles only if you ask |
| Left ~300 MB of working files behind per video | Cleans up after itself automatically |
| Same settings on every machine | Adapts to your laptop — smaller models get shorter writing batches and more revision passes, so they still finish properly |

---

## For developers

The app is a local HTTP server (`shortsmith/webapp.py`, standard library only)
over an interface-agnostic core (`shortsmith/control.py`). The source is plain
Python in `src` layout — edit anything, restart the app, your change applies.

```
shortsmith/
  story_planner.py   develops a brief into a premise, cast and beat sheet
  crews/story_crew.py writes the narration to those beats
  continuity.py      re-reads the draft for contradictions and strangers
  timing.py          duration model, calibrated against measured audio
  model_profiles.py  per-model tuning; hardware-aware quality settings
  hardware.py        local-only machine scan
  nodes/             the 5 pipeline stages
  webapp.py          the local app server
```

Useful commands:

```bash
python run.py --dry-run --seconds 45 --topic "your idea"
```

writes a story and prints it **without rendering**, in about two minutes — the
fastest way to judge story quality.

```bash
python -m shortsmith.hardware
```

prints what the hardware scan sees.

Discord is still supported as an optional remote control
(`python -m shortsmith.discord_bot`) for driving it from your phone.

---

Human review before publishing is deliberate and permanent — see
[COMPLIANCE.md](COMPLIANCE.md).
