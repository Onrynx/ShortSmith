"""Visual provider router.

One interface (base.VisualProvider); the implementations, in the order the canvas
node tries them:
  background (BackgroundProvider) — gameplay clips from assets/backgrounds/ (primary)
  ai_image   (AIImageProvider)    — free Pollinations images (optional, needs net)
  motion     (MotionProvider)     — generative-motion video, the never-fail floor
  wan2gp/cloud                    — optional AI-video hero clips (need setup/keys)
gradient_card() is a helper used for the break-glass still + thumbnails.
"""
from .ai_image import AIImageProvider
from .background import BackgroundProvider, background_clips
from .base import GEN_NEGATIVE, ProviderError, VisualProvider, gradient_card
from .motion import MotionProvider


__all__ = [
    "VisualProvider", "ProviderError", "gradient_card", "GEN_NEGATIVE",
    "MotionProvider", "AIImageProvider", "BackgroundProvider", "background_clips",
]
