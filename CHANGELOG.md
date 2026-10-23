# Changelog

## 2.3.0

**New**
- Everything a render needs is fetched in one step. Picking a writer model now
  also brings the video encoder, the narration voice and the caption timer, behind a single
  progress bar that ends with "everything is ready". They used to arrive
  separately, the first time a render happened to reach their stage, so a first
  video stalled twice for downloads nobody had mentioned — and those two
  bypassed the mobile-data warning entirely, because it only counted the one
  you picked. The warning now quotes the real total.
- One download, nothing to install. Shortsmith now ships with its own copy of
  Python and every library it needs, so the five-step setup — install Python,
  remember to tick Add to PATH, run a setup script — is gone. Unzip it,
  double-click it. Ollama is still a separate install, because it is a separate
  program that installs itself properly and may already be there.
- A download page that picks the file for the computer you are on, with the
  others one click away underneath in case it guesses wrong.
- Updates only fetch what changed. A release carries a manifest of every
  shipped file and its checksum; the app hashes what it has and downloads the
  difference — usually kilobytes instead of a whole fresh copy over the old
  one. Every file is verified against its published checksum before it is kept,
  the download is staged so a failure halfway cannot leave a half-updated
  install, and nothing you own is in a manifest to begin with. This works for
  every install now, not only ones cloned with git.
- The macOS download opens in a real window, not a browser tab. The pinned
  dependency list had been resolved on a Windows machine, which recorded only
  the Windows half of what the window library needs and left out the six
  packages macOS requires. Nothing would have crashed — Shortsmith falls back
  to your normal browser whenever a window cannot be drawn — so a Mac download
  would have looked like it worked while silently never giving anyone the app.
  The list is now resolved for every platform at once, and the build refuses to
  publish a bundle whose window cannot open. Linux stays on the browser by
  choice: its window toolkits would have to be compiled against the build
  machine's system libraries, which are not guaranteed to exist on yours.
- Bundles for macOS and Linux are built by GitHub, on GitHub's own Macs and
  Linux machines. A bundle carries compiled libraries, so it can only be
  assembled on the system it is for — which previously meant Windows was the
  only download that existed, and the page's macOS button was a 404. Publishing
  a release now builds all three and attaches them, and the download page picks
  them up by itself because it asks GitHub which assets exist.
- `scripts/release.py` cuts a release in one command, in the order that
  matters: checks, version, changelog, manifest, push, build, publish. The
  manifest has to be regenerated after the last edit and before the push, and
  getting that wrong fails quietly for everyone.
- Shortsmith notices how you are online. On Wi-Fi or a cable, model downloads
  start straight away. On mobile data, a phone hotspot, while roaming, or near
  a data cap, it says which it is and how big the download is, and waits for
  you to say yes. It asks the operating system about the link — never about
  what travels over it.
- Settings shows the connection next to your hardware, and Re-scan now
  re-checks hardware, free space and connection together.

## 2.2.0

**New**
- A Backgrounds section. The gameplay clips behind every video were always just
  a folder the README told you about: nothing showed what was in it, and an
  empty one changed how videos looked without saying why. Now you can see every
  clip with a frame from it, its length and its size, drag new ones in or click
  to choose, and remove the ones you are done with. Uploads stream straight to
  disk, so a 2 GB clip copies with a progress bar instead of being held in
  memory, and a file that is not really a video is refused rather than stored.

**Security**

This release is mostly one thing: closing the gap between what the app said
about itself and what it did. It said nothing leaves this computer, and three
different paths could still send your work elsewhere. It said localhost was
enough, and any website you visited could drive it.

- Uploads write to a randomly named scratch file created with O_EXCL, and a
  short upload is refused rather than kept. The old scratch name was entirely
  predictable — `<name>.mp4.part` — so a symlink planted there in advance was
  followed, and the upload's bytes landed wherever it pointed. A transfer that
  ends early now fails instead of leaving a truncated video that would surface
  much later as a render ending mid-sentence.
- A stalled upload no longer holds a connection slot for half an hour. The
  timeout was per-read, not per-transfer, so a client that promised two
  gigabytes and went quiet kept a thread until it expired.
- At capacity the server answers 503 instead of dropping the connection, a
  token containing non-ASCII gives 403 instead of raising, `"ids": 42` gives
  400 instead of raising, an Origin on the wrong scheme or port is refused even
  with a valid token, and `bytes=-3` returns the last three bytes rather than
  the first four.
- The local server now refuses anything that is not the app's own page. It
  checks the request came to a loopback address, that any Origin is ours, and
  that it carries a per-run token only the page we served knows. Before this, a
  site you happened to be visiting could reach http://127.0.0.1 from your
  browser and drive the whole app behind your back — including deleting every
  video in the Library. A foreign hostname pointed at 127.0.0.1 could read your
  data back as well.
- Shortsmith no longer sends anything anywhere. Gone: an online image generator
  that was ON by default and received every scene description whenever the
  backgrounds folder was empty; a Gemini fallback that sent the brief and every
  draft to Google whenever Ollama happened to be down and an API key was
  present; hosted ElevenLabs and TikTok voices that uploaded the entire script;
  a YouTube upload path, including the environment variable that made it
  publish PUBLICLY; and a Discord webhook carrying stage events. The only
  traffic left is Ollama on this machine, the update check, and the one-time
  voice-model download.
- The Discord bot, its language agent and the login-autostart installer are
  removed. Nothing in the app reached them, and between them they were a hidden
  startup entry, an outbound connection, and the only reason a live bot token
  needed to exist.
- Every identifier that becomes a filename is validated before it is joined,
  and every resulting path is checked to still be inside the folder it was
  meant for. A percent-encoded slash used to survive the route's own split and
  turn back into a separator afterwards, which let a request read records
  outside the output folder and let a gate decision write a file outside its
  own directory.
- Cancelling a render checks the process is still the render before killing it.
  A crashed render leaves its pid file behind, the operating system hands that
  number to something else soon enough, and the old code would have terminated
  whatever inherited it — with its children, on Windows.
- Only one render can start at a time, decided under a lock. The check and the
  launch were two steps with seconds between them, so two clicks could put two
  renders on the GPU.
- The queue has a ceiling of 20. "At most ten per click" still let ten clicks
  stack a hundred renders.
- Requests must carry a JSON object. Malformed JSON used to run the route with
  empty arguments, and a JSON array raised a 500 instead of being refused.
- Connections get a read timeout and the server a ceiling on how many it holds
  at once, so a socket that opens and says nothing no longer keeps a thread for
  as long as it likes. Oversized bodies are refused with 413, unsatisfiable
  ranges with 416.
- Metadata sent to the app is checked against the shape a record is allowed to
  have, instead of being stored as it arrives.
- Video ids are escaped for JavaScript as well as for HTML before they reach a
  click handler. They are filenames, and a record copied from another machine
  can carry a quote or a backslash the HTML escaper never touched.
- The update button checks WHICH repository it is about to pull from, and
  reports whether the commit it moved to was signed. A git config pointed
  somewhere else was the one way that button could have installed code from a
  repository you never chose.
- Dependencies are pinned to exact versions with a hash-pinned lockfile
  covering the whole tree — 57 packages, every one checked against the advisory
  database. Install with `pip install --require-hashes -r
  requirements-lock.txt`.
- Kokoro's voice weights are checked against a pinned SHA-256 before they are
  loaded, and land on a temporary file until they pass.
- On Windows the listening socket no longer sets SO_REUSEADDR, which there lets
  a second process bind the same port.
- The app says so, once, when the ffmpeg it found lives in a folder your own
  account can write to.
- Model downloads only accept tags from the app's own menu.
- The changelog is escaped before it is put on the page, instead of after.
- `.env.example` no longer asks for credentials belonging to removed features.

**Fixed**
- Nothing could render on a machine without ffmpeg, and the bundle never had
  one. It cuts the clips, assembles the video, reads durations and makes the
  thumbnail — so a clean computer got all the way through picking a model, nine
  gigabytes of downloads and several minutes of writing before failing with
  "FFmpeg not found on PATH". It is now fetched during setup like everything
  else, into the app's own folder, needing no administrator rights and touching
  nothing outside it. A machine that already has ffmpeg keeps using its own.
- The hardware scan is cached per machine now. The cache carried no record of
  which computer wrote it, so an install folder shared between systems handed
  one of them the other's hardware — and every model recommendation after that
  was for the wrong computer.
- The download page only offers builds that exist. It listed Windows, macOS and
  Linux unconditionally while only Windows had ever been built, so a Mac got a
  404 from the main button and a phone got two dead links. It now asks GitHub
  what is actually attached, says plainly when a platform has no build yet, and
  tells a phone that Shortsmith needs a computer.
- Updates could never have worked. Git was converting line endings on checkout,
  so the manifest recorded hashes of CRLF files while the updater downloaded
  the LF versions GitHub serves — every text file failed its checksum. A
  `.gitattributes` now pins line endings so the working copy, the repository
  and what GitHub serves are the same bytes.
- Approved videos carried a duplicate. Every one had a second, compressed
  `.preview.mp4` beside it — built to fit Discord's 10 MB upload cap by a
  feature since removed, and never cleaned up. A finished video is four files:
  the mp4, its thumbnail, its record and its report. Anything else sharing the
  name is swept at startup.
- Cancelling a queued job crashed on a malformed id, returning a 500 instead of
  a refusal.
- Update checking told you an update was waiting when there was not. A ZIP
  install was never compared against anything, so it was told to download the
  latest forever — including right after doing so. A git copy was compared only
  against the last ten commits, so one commit of your own made it claim an
  update existed permanently. It now compares published version numbers, knows
  the difference between behind, current and ahead, and reads the version from
  disk so an installed update registers immediately.
- A window left open while the app restarts says so and offers to reload,
  instead of silently failing every request forever.
- A status update with an unusual lane name could raise and take a whole render
  down on Windows.

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
