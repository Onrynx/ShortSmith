"""StoryForge — a personal, fully-autonomous multi-agent YouTube video system.

Two videos/day via two engine-isolated lanes (night/local Wan2GP, day/cloud),
seven agents (Scout, Scribe, Critic, script, voice, canvas, cutter, herald),
orchestrated by CrewAI (creative crew) inside LangGraph (checkpointed assembly).

Design principle baked into every module: **degrade, never die.** The core
pipeline depends only on the Python stdlib + Pillow + numpy + FFmpeg. Every heavy
dependency (CrewAI, LangGraph, kokoro-onnx, faster-whisper, FastAPI, requests,
pywebpush) is optional and guarded — real implementation when present, graceful
fallback when absent. That is what lets MOCK mode run end-to-end with zero keys,
zero network, and zero GPU and still emit a real .mp4.
"""

__version__ = "1.0.0"
