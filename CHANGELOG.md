# Changelog

## 2.1.0

**New**
- Guided mode now actually stops. The pipeline pauses between writing and
  rendering, shows you the whole script with its length and shape, and waits.
  Render it, send it back with a note, or throw it away — before the fifteen
  minutes of voice and video are spent on it.
- Full auto checks its own work: it measures the finished file (plays, length,
  audio that is not silence, captions, every scene present, no visible
  fallback) and files what passes in the Library. Anything that fails lands in
  Review with the reasons on the card.
- Several versions of one idea. Ask for two or three and each gets a different
  angle on the same brief — a different hook, shape and script — rendered one
  after another, with the queue visible and cancellable while it works.
- Posting metadata written from the finished video: five title options, a real
  description, hashtags, keywords and a caption, generated from that video's
  own script and stored on that video's record. Copy any field, pick a title,
  or have it written again.
- Carry on from where it stopped. A render that crashes keeps its checkpoint,
  so the app offers to resume it — the written script and the recorded
  narration are reused instead of being paid for twice.
- Explainers get fact-checked. Claims are pulled out of the finished script and
  put to the model one at a time, twice, and only where both passes agree that
  something is wrong is the line corrected. Anything it could not settle is
  shown to you rather than quietly changed. Simplifications are left alone, and
  a myth the script is about to debunk is not treated as a claim.
- The Library is a grid of files now. Click to select, double-click to open
  details, ⋮ for the rest. Shift and Ctrl select ranges, Delete and Enter do
  what you expect, and deleting always asks first.

**Changed**
- Express says what it does: no stop for the script, and the finished video
  still waits for you in Review.
- Video playback moved into the details drawer, so the list refreshing can
  never interrupt something you are watching.

**Fixed**
- A status update with an unusual lane name could raise and take a whole render
  down on Windows.

## 2.0.0

**New**
- A real app with its own window and icon — no terminal, no Discord setup.
- Startup brings the AI engine up with the app and shuts it down after, so
  nothing sits in memory all day. Real progress while it loads (and a game).
- Ask for any length — "45 seconds" — and the story is written, measured
  against the real narration, and revised until it fits.
- Explainer mode: factual subjects are explained rather than turned into a
  story about someone discovering them.
- Voice preview before committing to a render.
- Review videos in the app, select several, approve or delete in one go.
- Library of everything you kept.
- Settings shows what your computer can run and downloads only the model
  you choose.

**Changed**
- Stories plan their own structure instead of following one fixed arc.
- Captions take their words from the script, so they are always spelled right.
- Narration only — no background music or placeholder tones.
- Working files are deleted after each video (~300 MB a time).
- Tuning adapts to your machine; smaller models get shorter writing batches
  and more revision passes.
- Real-life drama is the default; fantasy only if you ask.

**Removed**
- Night/day lanes and the daily schedule.
- Odysseus, Wan2GP, cloud video and stock-footage providers.
