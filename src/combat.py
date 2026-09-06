"""Work out whether you are fighting right now.

You asked for this to be exact: even 1% adrenaline counts as fighting.

Measuring the yellow bar cannot deliver that on its own. The bar is 89 pixels
wide, so 1% is under one pixel. Instead the main signal is the "0%" text next to
it, compared against a photo of that text taken while adrenaline really was zero:

    saved:  [ 0% ]     live: [ 0% ]  -> identical   -> NOT fighting
    saved:  [ 0% ]     live: [ 1% ]  -> different   -> FIGHTING

Measured on the live client, two grabs of an unchanged "0%" differ by 0.000,
while a changed glyph differs by about 17. The default threshold of 4.0 sits far
above the noise and far below a real change.

Two more signals back it up:
  * any coloured pixel at all in the adrenaline bar (it is completely empty at 0%)
  * your health going down in the last few seconds

Separately, targeting uses ``target_bar_visible`` — the top target-info bar
frame (dark charcoal fill + tan border), not the green HP fill and not
adrenaline — to know when you already have a target. Tunables live under
``target_bar`` / ``combat`` in config.default.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from . import bars

# Fallbacks only if config omits keys (full catalogue is config.default.json).
_DEFAULT_TARGET_BAR = {
    "roi_frac": [0.20, 0.00, 0.80, 0.10],
    "interior_bgr_low": [8, 8, 8],
    "interior_bgr_high": [35, 38, 42],
    "border_bgr_low": [20, 35, 45],
    "border_bgr_high": [80, 110, 140],
    "border_r_minus_b_min": 15,
    "min_width_px": 150,
    "min_height_px": 8,
    "max_height_px": 50,
    "min_border_px": 40,
    "min_roi_height_px": 40,
    "morph_kernel_w": 15,
}


def to_signature(img_bgr: np.ndarray) -> np.ndarray:
    """The comparable form of the adrenaline-text box."""
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)


def text_differs(current: np.ndarray, zero_ref: np.ndarray,
                 threshold: float) -> tuple[bool, float]:
    """Compare the live text box against the saved 0% photo."""
    if zero_ref is None or current.shape != zero_ref.shape:
        return False, 0.0
    score = float(np.abs(current - zero_ref).mean())
    return score > threshold, score


def _tbar(cfg: dict | None, key: str):
    src = cfg if isinstance(cfg, dict) else {}
    if key in src:
        return src[key]
    return _DEFAULT_TARGET_BAR[key]


def target_bar_visible(frame_bgr: np.ndarray,
                       target_bar_cfg: dict | None = None) -> bool:
    """True when the top target-info bar frame is on screen.

    Matches the dark charcoal shell + tan/bronze border of the whole top bar —
    not the bright green HP strip. Colours / ROI come from ``target_bar`` in
    config (see config.default.json).
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return False
    tcfg = target_bar_cfg if isinstance(target_bar_cfg, dict) else {}
    fh, fw = frame_bgr.shape[:2]
    rx0, ry0, rx1, ry1 = (float(v) for v in _tbar(tcfg, "roi_frac"))
    x0 = int(fw * rx0)
    x1 = int(fw * rx1)
    y0 = int(fh * ry0)
    y1 = max(int(fh * ry1), int(_tbar(tcfg, "min_roi_height_px")))
    min_w = int(_tbar(tcfg, "min_width_px"))
    min_h = int(_tbar(tcfg, "min_height_px"))
    max_h = int(_tbar(tcfg, "max_height_px"))
    if x1 - x0 < min_w or y1 - y0 < min_h:
        return False

    roi = frame_bgr[y0:y1, x0:x1]
    b = roi[:, :, 0]
    g = roi[:, :, 1]
    r = roi[:, :, 2]

    ilo = np.array(_tbar(tcfg, "interior_bgr_low"), dtype=np.int16)
    ihi = np.array(_tbar(tcfg, "interior_bgr_high"), dtype=np.int16)
    blo = np.array(_tbar(tcfg, "border_bgr_low"), dtype=np.int16)
    bhi = np.array(_tbar(tcfg, "border_bgr_high"), dtype=np.int16)
    r_minus_b = int(_tbar(tcfg, "border_r_minus_b_min"))

    dark = (
        (b >= ilo[0]) & (b <= ihi[0])
        & (g >= ilo[1]) & (g <= ihi[1])
        & (r >= ilo[2]) & (r <= ihi[2])
    )
    border = (
        (b >= blo[0]) & (b <= bhi[0])
        & (g >= blo[1]) & (g <= bhi[1])
        & (r >= blo[2]) & (r <= bhi[2])
        & (r.astype(np.int16) > b.astype(np.int16) + r_minus_b)
        & (g > b)
    )

    dark_u8 = dark.astype(np.uint8) * 255
    kw = max(1, int(_tbar(tcfg, "morph_kernel_w")))
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 1))
    dark_u8 = cv2.morphologyEx(dark_u8, cv2.MORPH_CLOSE, ker)
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(dark_u8, 8)
    min_border = int(_tbar(tcfg, "min_border_px"))
    for i in range(1, n):
        ww = int(stats[i, cv2.CC_STAT_WIDTH])
        hh = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])
        if ww < min_w:
            continue
        if hh < min_h or hh > max_h:
            continue
        if area < min_w * 6:
            continue
        bx = int(stats[i, cv2.CC_STAT_LEFT])
        by = int(stats[i, cv2.CC_STAT_TOP])
        pad = 3
        ya = max(0, by - pad)
        yb = min(roi.shape[0], by + hh + pad)
        xa = max(0, bx - pad)
        xb = min(roi.shape[1], bx + ww + pad)
        border_px = int(border[ya:yb, xa:xb].sum())
        if border_px >= min_border:
            return True
    return False


@dataclass
class CombatDetector:
    zero_ref: np.ndarray | None = None
    text_threshold: float = 4.0
    hp_drop_window: float = 3.0
    hp_drop_min: float = 0.005

    _last_hp: float | None = field(default=None, init=False)
    _last_drop_at: float = field(default=-1e9, init=False)
    last_score: float = field(default=0.0, init=False)
    last_reason: str = field(default="", init=False)

    def update(self, adren_text_bgr: np.ndarray, adren_bar_bgr: np.ndarray,
               hp_frac: float, now: float) -> bool:
        """Feed in this tick's pixels and get back: are we fighting?"""
        if self._last_hp is not None and (self._last_hp - hp_frac) > self.hp_drop_min:
            self._last_drop_at = now
        self._last_hp = hp_frac
        hp_dropping = (now - self._last_drop_at) <= self.hp_drop_window

        differs, score = text_differs(to_signature(adren_text_bgr),
                                      self.zero_ref, self.text_threshold)
        self.last_score = score
        bar_lit = bars.has_any_yellow(adren_bar_bgr)

        reasons = []
        if differs:
            reasons.append(f"adrenaline text changed ({score:.1f})")
        if bar_lit:
            reasons.append("adrenaline bar lit")
        if hp_dropping:
            reasons.append("health dropping")
        self.last_reason = ", ".join(reasons) or "idle"

        return bool(differs or bar_lit or hp_dropping)
