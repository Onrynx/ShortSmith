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

**Security**
- Every identifier that becomes a filename is validated before it is joined,
  and every resulting path is checked to still be inside the folder it was
  meant for. A percent-encoded slash used to survive the route's own split and
  turn back into a separator afterwards, which let a request read JSON records
  outside the output folder and let a gate decision write a file outside its
  own directory.
- Cancelling a render checks the process is still the render before killing
  it. A crashed render leaves its pid file behind, the operating system hands
  that number to something else soon enough, and the old code would have
  terminated whatever inherited it — with its children, on Windows.
- Requests must carry a JSON object. Malformed JSON used to run the route with
  empty arguments, and a JSON array raised a 500 instead of being refused.
- Connections get a read timeout and the server a ceiling on how many it will
  hold at once, so a socket that opens and says nothing no longer keeps a
  thread for as long as it likes. Oversized bodies are refused with 413 and
  unsatisfiable ranges with 416.
- On Windows the listening socket no longer sets SO_REUSEADDR, which there
  lets a second process bind the same port.
- Narration is synthesised on this machine only. The hosted ElevenLabs and
  TikTok engines are gone: an API key in a config file used to send the whole
  script to a third party with nothing on screen to say so.
- Nothing uploads itself. The YouTube path is gone, including the environment
  variable that made it upload PUBLICLY.
- Stage events no longer POST to a Discord webhook.
- The Discord bot, its language agent and the login-autostart installer are
  removed. Nothing in the app reached them, and between them they were a
  hidden startup entry, an outbound connection and the only reason a live bot
  token needed to exist.
- Kokoro's voice weights are checked against a pinned SHA-256 before they are
  loaded, and land on a temporary file until they pass.
- Dependencies are pinned to exact versions instead of floating on `>=`.
- `.env.example` no longer asks for credentials belonging to removed features.

- The local server now refuses anything that is not the app's own page. It
  checks that the request came to a loopback address, that any Origin is ours,
  and that it carries a per-run token that only the page we served knows.
  Before this, a website you happened to be visiting could reach
  http://127.0.0.1 from your browser and drive the whole app behind your back
  — including deleting every video in the Library. A foreign hostname pointed
  at 127.0.0.1 could also read your data back.
- The changelog is escaped before it is put on the page, instead of after.
- Model downloads only accept tags from the app's own menu.
- Request bodies are capped, and the folder check that keeps the file server
  inside app/ no longer compares paths as strings.

**Fixed**
- Update checking told you an update was waiting when there was not. A ZIP
  install was never compared against anything, so it was told to download the
  latest forever — including right after doing so. A git copy was compared
  only against the last ten commits, so one commit of your own made it claim
  an update existed permanently. It now compares published version numbers,
  knows the difference between behind, current and ahead, and reads the
  version from disk so an installed update registers immediately.
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
