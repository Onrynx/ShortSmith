# Shortsmith V2

Make YouTube Shorts on your own laptop. You type an idea — or nothing at all —
and a few minutes later a finished vertical video is waiting for you to approve:
original story, AI narration, gameplay background, word-by-word captions.

**Everything runs on your computer.** No account, no subscription, no API keys,
no cloud. Nothing you make is uploaded anywhere until you decide to post it.

---

## Get it

### [⬇ Download for Windows](https://github.com/Onrynx/ShortSmith/releases/latest/download/Shortsmith-Windows.zip)

That link always gives you the newest release — it is not tied to a version
number, so it keeps working after every update.
[All versions](https://github.com/Onrynx/ShortSmith/releases).

> **macOS and Linux are not built yet.** Shortsmith runs on them — the code is
> the same — but each one needs its bundle built on that kind of machine, and
> that has not been done. If you are on a Mac or Linux and want it, open an
> issue and say so.
>
> **There is no phone version.** Shortsmith renders video on the machine it
> runs on.

Then, once:

1. **Unzip it** anywhere (your Desktop is fine) and double-click **Shortsmith**.
2. **Install Ollama** from [ollama.com/download](https://ollama.com/download).
   That is the engine that writes the stories. It installs itself like any
   normal app, and Shortsmith starts and stops it for you.

That is the whole install. **There is no Python to install and nothing to
type** — the download carries its own copy of everything it needs.

> **Windows may warn you** that it does not recognise the app, because the
> download is not signed by a company Microsoft has on file. Click **More info
> → Run anyway**. You can read every line of what it runs in this repository.

### Pick a writer model

The first time you open it, Settings shows every model with what it needs and
what you get. Your computer is checked first — memory, graphics and free disk —
and anything that will not fit is greyed out with the reason.

**Only the model you choose is downloaded.** If you are on mobile data or a
phone hotspot, Shortsmith says so and asks before spending gigabytes of it.

| | |
|---|---|
| **Qwen 2.5 14B** | 8.4 GB — richest stories, best pacing. Wants 16 GB of memory or a decent GPU. |
| **Qwen 2.5 7B** | 4.7 GB — good, slightly flatter plots. |
| **Llama 3.2 3B** | 2 GB — light, weaker continuity, simpler stories. |
| **Qwen 2.5 1.5B** | 1 GB — minimum. Old laptops only. |

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

**The window doesn't open.** Double-click **Shortsmith (show errors)** in the
folder instead — it opens in your browser and leaves the reason on screen,
which works exactly the same.

**"Ollama unreachable".** Ollama isn't running. Open it from your Start menu or
Applications; it sits quietly in the tray or menu bar.

**Videos have no gameplay behind them.** No clips in `assets/backgrounds`, or
they're in a format ffmpeg can't read. Try a normal `.mp4`.

**It's slow.** The story is written by an AI on your own machine. On a laptop
without a strong graphics card, 12–15 minutes per video is normal. Choosing a
smaller model in Settings makes it quicker.

---

## Updates

The **Updates** tab checks whether a newer version has been published and shows
what changed. Installing one **only fetches the files that actually differ** —
usually a few kilobytes — rather than downloading a whole fresh copy over the
old one. Every file is checked against its published checksum before it is kept,
and your videos, clips, models and settings are not part of an update at all.

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

There is no Discord bot any more, and no remote control of any kind.
Shortsmith runs in its own window on this machine and talks to nothing but
Ollama on localhost — plus a version check against GitHub, and a one-time
download of the voice model.

---

Human review before publishing is deliberate and permanent — see
[COMPLIANCE.md](COMPLIANCE.md).
