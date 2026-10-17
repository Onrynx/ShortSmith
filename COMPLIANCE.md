# Compliance — YouTube 2025–2026 "inauthentic / mass-produced content" policy

YouTube demonetizes channels that publish **mass-produced, repetitive, or inauthentic**
content with no meaningful human involvement, and requires creators to **disclose
altered or synthetic (AI) content**. StoryForge is built so a human stays in the
loop and every safeguard is enforced in code, not just documented.

## Safeguard → policy mapping

| Policy concern | StoryForge safeguard | Where |
| --- | --- | --- |
| **Disclose synthetic content** | The line *"This video contains AI-generated narration and imagery."* is appended to **every** description, and cannot be removed by the model. | `storyforge/schema.py` `ensure_disclosure()`, enforced in `nodes/script.py` + `nodes/herald.py` |
| **Self-declare altered/synthetic on upload** | Uploads set the self-declared synthetic-content disclosure and go up **PRIVATE**, never public. | `nodes/herald.py` `_upload_youtube()` |
| **No invented facts / misinformation** | The **Critic** agent flags unverifiable claims for softening; the Scribe is instructed to never invent facts and to make honest uncertainty the hook. Canned scripts model this ("historians still argue", "we don't actually know"). | `crews/story_crew.py` |
| **No copyrighted IP / real people** | Image prompts must exclude text, logos, real people, and copyrighted characters; `", no text, no watermark, no logo, no real people"` is appended to every generative prompt; Gaming Lore evokes **genre** aesthetics, never trademarked characters. | `providers/base.py` `GEN_NEGATIVE`, prompts in `crews/story_crew.py`, `config/*.yaml` `banned:` |
| **Not mass-produced / low-effort** | Two curated videos/day (not hundreds), each a single high-curiosity story with a crafted hook, cinematic visuals, a real voice, and burned captions. | whole pipeline |
| **Meaningful human involvement (the big one)** | A **mandatory human approval gate**: every video lands in `pending/` with a thumbnail, full script, and an approval report. Nothing moves to `approved/` — and nothing is uploadable — until you review and approve it in Discord (`!approve`). | `nodes/herald.py`, `storyforge/discord_bot.py`, `output/<channel>/pending → approved` |
| **Banned topics per channel** | Each channel declares a `banned:` list the Scout must respect (e.g. gore, living people, leaks, copyrighted likenesses). | `config/*.yaml` |

## The approval gate is not optional

Fully hands-off "AI channels" are exactly what the 2025–2026 policy targets. StoryForge
therefore **defaults to human approval** and keeps it in the flow permanently:

1. The pipeline writes the finished `.mp4` + `*.report.txt` + metadata into
   `output/<channel>/pending/` — **it is never auto-published**.
2. You run `!review 1` in Discord, watch the Short, read the script and the
   compliance checklist in the report, then `!approve` or `!reject`.
3. Approve moves the files to `approved/`. Only an explicitly approved, explicitly
   `--publish`ed video is uploaded — and only ever as **PRIVATE**, so a human still
   flips the final visibility switch in YouTube Studio.

This human checkpoint is the single most important safeguard for staying monetizable,
and it is designed to stay.

## Attribution & likeness

- The narrator voice is **Kokoro** (kokoro-onnx, open weights, commercial-safe) — a
  synthetic cinematic narrator, **not** a clone of any real person.
- The story writer runs locally via Ollama; an optional backend (Gemini)
  are LLM backends only and never appear in a produced video.
