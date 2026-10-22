"""Shortsmith — a local YouTube Shorts factory with its own desktop app.

Pipeline: script -> voice -> canvas -> cutter -> herald. Stories are written by a
local model via Ollama; narration by Kokoro; visuals over gameplay backgrounds;
captions synced with faster-whisper; assembled by FFmpeg; reviewed and approved by
you in the app.

Design principle baked into every module: **degrade, never die.** The core depends
only on the Python stdlib + Pillow + numpy + FFmpeg + Ollama. Every heavier piece
(Kokoro and whisper for quality, LangGraph for orchestration) is optional and
guarded — real when present, graceful fallback when absent. That is what lets
MOCK mode run end-to-end with zero keys, zero network and zero GPU and still
emit a real .mp4.

There are no cloud APIs. Earlier versions had several, each reachable from a
line in a config file; none of them survive.
"""

__version__ = "2.3.0"
