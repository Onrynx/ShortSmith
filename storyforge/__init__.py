"""StoryForge — a personal, local YouTube Shorts factory controlled from Discord.

Pipeline: script -> voice -> canvas -> cutter -> herald. Stories are written by a
local model via Ollama; narration by Kokoro; visuals over gameplay backgrounds;
captions synced with faster-whisper; assembled by FFmpeg; reviewed and approved by
you in Discord.

Design principle baked into every module: **degrade, never die.** The core depends
only on the Python stdlib + Pillow + numpy + FFmpeg + Ollama. Every heavier piece
(Kokoro/whisper for quality, and the optional LangGraph,
cloud APIs) is optional and guarded — real when present, graceful fallback when
absent. That is what lets MOCK mode run end-to-end with zero keys, zero network,
and zero GPU and still emit a real .mp4.
"""

__version__ = "1.0.0"
