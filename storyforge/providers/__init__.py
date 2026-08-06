"""Visual provider router.

One interface (base.VisualProvider); the implementations, in the order the canvas
node tries them:
  background (BackgroundProvider) — gameplay clips from assets/backgrounds/ (primary)
  stock      (StockProvider)      — Pexels + Pixabay (optional, needs keys)
  ai_image   (AIImageProvider)    — free Pollinations images (optional, needs net)
  motion     (MotionProvider)     — generative-motion video, the never-fail floor
  wan2gp/cloud                    — optional AI-video hero clips (need setup/keys)
gradient_card() is a helper used for the break-glass still + thumbnails.
"""
from .ai_image import AIImageProvider
from .background import BackgroundProvider, background_clips
from .base import GEN_NEGATIVE, ProviderError, VisualProvider, gradient_card
from .cloud import CapReached, CloudProvider, cap_remaining
from .motion import MotionProvider
from .stock import StockProvider, keywords_from_scene
from .wan2gp import DeferredRender, Wan2GPProvider


def lane_video_provider(lane: str, cfg: dict) -> VisualProvider:
    """Optional AI-video hero-clip provider: night -> Wan2GP, day -> cloud."""
    return Wan2GPProvider(cfg) if lane == "night" else CloudProvider(cfg)


__all__ = [
    "VisualProvider", "ProviderError", "gradient_card", "GEN_NEGATIVE",
    "MotionProvider", "AIImageProvider", "BackgroundProvider", "background_clips",
    "StockProvider", "keywords_from_scene", "Wan2GPProvider", "DeferredRender",
    "CloudProvider", "CapReached", "cap_remaining", "lane_video_provider",
]
