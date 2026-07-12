"""Visual provider router.

One interface (base.VisualProvider); four implementations:
  gradient (GradientProvider) — last resort / mock (always available, no network)
  stock    (StockProvider)    — Pexels + Pixabay, unlimited free backfill
  wan2gp   (Wan2GPProvider)   — night lane, local Wan 2.2 @ 480p
  cloud    (CloudProvider)    — day lane, Veo / Seedance with a daily cap
"""
from .base import (GEN_NEGATIVE, GradientProvider, ProviderError, VisualProvider,
                   gradient_card)
from .cloud import CapReached, CloudProvider, cap_remaining
from .motion import MotionProvider
from .stock import StockProvider, keywords_from_scene
from .wan2gp import DeferredRender, Wan2GPProvider


def lane_video_provider(lane: str, cfg: dict) -> VisualProvider:
    """The hero-clip provider for a lane: night -> Wan2GP, day -> cloud."""
    return Wan2GPProvider(cfg) if lane == "night" else CloudProvider(cfg)


__all__ = [
    "VisualProvider", "ProviderError", "GradientProvider", "gradient_card",
    "GEN_NEGATIVE", "MotionProvider", "StockProvider", "keywords_from_scene",
    "Wan2GPProvider", "DeferredRender", "CloudProvider", "CapReached",
    "cap_remaining", "lane_video_provider",
]
