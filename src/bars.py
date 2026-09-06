"""Read the health and adrenaline bars from raw pixels.

We never read the text "358/1,200". Instead we look at the coloured bar itself
and work out how far along it the colour stops. That is pure arithmetic, takes
well under a millisecond, and does not care what the numbers say.

    [########..................]   colour stops 30% across  ->  health = 30%
"""

from __future__ import annotations

import cv2
import numpy as np

# Hue ranges in OpenCV's 0-179 scale.
_RED_LO, _RED_HI = 12, 168        # red wraps around, so it is <=12 OR >=168
_YELLOW_LO, _YELLOW_HI = 15, 45   # adrenaline's orange/yellow


def _mask_red(hsv: np.ndarray) -> np.ndarray:
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return ((h <= _RED_LO) | (h >= _RED_HI)) & (s > 110) & (v > 60)


def _mask_yellow(hsv: np.ndarray) -> np.ndarray:
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return (h >= _YELLOW_LO) & (h <= _YELLOW_HI) & (s > 100) & (v > 90)


def _fill_fraction(img_bgr: np.ndarray, mask_fn, inner_width: int | None) -> float:
    """How much of the bar is coloured in, as 0.0 - 1.0.

    We count coloured pixels per row and take the busiest row, which shrugs off
    the blurry top and bottom edges of the bar.

    `inner_width` is the true pixel width of the bar's scale. It can be a little
    smaller than the box you drew (the bar has rounded end caps that are not part
    of the scale), so calibration works it out exactly and stores it.
    """
    if img_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = mask_fn(hsv)
    per_row = mask.sum(axis=1)
    if per_row.max() == 0:
        return 0.0
    width = inner_width or img_bgr.shape[1]
    return float(min(1.0, per_row.max() / width))


def health_fraction(img_bgr: np.ndarray, inner_width: int | None = None) -> float:
    """0.0 - 1.0 of the red health bar."""
    return _fill_fraction(img_bgr, _mask_red, inner_width)


def adrenaline_fraction(img_bgr: np.ndarray, inner_width: int | None = None) -> float:
    """0.0 - 1.0 of the yellow adrenaline bar."""
    return _fill_fraction(img_bgr, _mask_yellow, inner_width)


def has_any_yellow(img_bgr: np.ndarray, min_pixels: int = 2) -> bool:
    """True if the adrenaline bar has ANY colour in it.

    At exactly 0% adrenaline this bar is completely empty (verified against a
    live capture: zero yellow pixels), so even a couple of coloured pixels means
    adrenaline has left zero.
    """
    if img_bgr.size == 0:
        return False
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    return int(_mask_yellow(hsv).sum()) >= min_pixels
