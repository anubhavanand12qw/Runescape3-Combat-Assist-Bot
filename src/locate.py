"""Automatically find the health and adrenaline bars in a screenshot.

RS3's status row has a very distinctive shape: four dark, thin, equal-width
tracks (health, adrenaline, prayer, summoning) sitting side by side on the same
row. Nothing else on screen looks like that, so we search for exactly that
pattern instead of asking you to click.

Measured on the live client, those four tracks came out 89, 89, 90 and 90 pixels
wide, all on the same row - which is the pattern this looks for.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MIN_TRACK_W = 55
MAX_TRACK_W = 220
MIN_TRACKS = 3          # health, adrenaline, prayer at least
DARK_V = 70             # a track's empty part is darker than this


@dataclass
class Located:
    hp_bar: tuple[int, int, int, int]      # x, y, w, h in image pixels
    adren_bar: tuple[int, int, int, int]
    adren_text: tuple[int, int, int, int]
    row_y: int
    confidence: str


def _dark_runs(dark_row: np.ndarray) -> list[tuple[int, int]]:
    """Horizontal stretches of dark pixels that are the right size for a track."""
    runs, start = [], None
    for i, d in enumerate(dark_row):
        if d and start is None:
            start = i
        elif not d and start is not None:
            if MIN_TRACK_W <= i - start <= MAX_TRACK_W:
                runs.append((start, i - 1))
            start = None
    if start is not None and MIN_TRACK_W <= len(dark_row) - start <= MAX_TRACK_W:
        runs.append((start, len(dark_row) - 1))
    return runs


def _red_rows(hsv: np.ndarray, x0: int, x1: int,
              y_lo: int, y_hi: int) -> list[int]:
    """Rows inside the health track that actually contain red fill.

    The red fill is what pins down the bar's true top and bottom. The dark panel
    behind the bars is taller and wider than the bars themselves, so trusting the
    dark area alone gives a box that is too big.
    """
    H, S, V = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    rows = []
    for y in range(max(0, y_lo), min(hsv.shape[0], y_hi)):
        seg = slice(x0, x1 + 1)
        red = (((H[y, seg] <= 12) | (H[y, seg] >= 168))
               & (S[y, seg] > 110) & (V[y, seg] > 60))
        if red.sum() >= 3:
            rows.append(y)
    return rows


def find_bars(img_bgr: np.ndarray) -> Located | None:
    """Find the status bars in a full game-window screenshot.

    Search is constrained to the bottom-centre band so chat, inventory, and
    other dark UI strips cannot be mistaken for the four status tracks.
    """
    h, w = img_bgr.shape[:2]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    V = hsv[:, :, 2]

    # Status row is in the lower-centre band (often ~82–96% down). Start above
    # 0.85 so windowed layouts with a dock/title chrome still match.
    y_start = int(h * 0.80)
    x_lo, x_hi = int(w * 0.28), int(w * 0.72)
    best: tuple[int, list[tuple[int, int]], float] | None = None

    for y in range(y_start, h):
        # Only consider dark runs inside the centre band.
        band = np.zeros(w, dtype=bool)
        band[x_lo:x_hi] = V[y, x_lo:x_hi] < DARK_V
        runs = _dark_runs(band)
        if len(runs) < MIN_TRACKS:
            continue
        # Keep only runs whose widths agree with each other - the four RS3 bars
        # are all the same size, which is what makes this pattern unmistakable.
        widths = np.array([b - a + 1 for a, b in runs])
        med = float(np.median(widths))
        keep = [r for r, wd in zip(runs, widths) if abs(wd - med) <= max(4, med * 0.08)]
        # Prefer the left-most cluster of matching tracks in the status zone
        # (health is the leftmost of the four).
        keep = [r for r in keep if r[0] >= x_lo and r[1] <= x_hi]
        # Action-bar slot gutters also form equal dark runs — reject crowded rows.
        if len(keep) < MIN_TRACKS or len(keep) > 5:
            continue
        # Prefer exactly four tracks near the usual health x (~36% width).
        target = w * 0.365
        score = len(keep) * 10.0 - abs(keep[0][0] - target) * 0.05
        if best is None or score > best[2]:
            best = (y, keep, score)

    if best is None:
        return None

    row_y, runs, _score = best
    runs.sort()
    # Health is the leftmost of the equal-width tracks in the status row.
    coarse_hx0, coarse_hx1 = runs[0]

    # Refine using the red fill. The coarse row can sit just above the bars, in
    # the wider dark panel, which would give a box several pixels too big.
    red_rows = _red_rows(hsv, coarse_hx0, coarse_hx1, row_y - 12, row_y + 13)
    if red_rows:
        top, bottom = red_rows[0], red_rows[-1]
        # Sample the bar's own border row, just outside the fill. On a fill row
        # the red itself breaks the dark stretch, and a row further up is the
        # wider panel behind the bars - only the border row gives true edges.
        for probe in (top - 1, bottom + 1):
            if not (0 <= probe < V.shape[0]):
                continue
            band = np.zeros(w, dtype=bool)
            band[x_lo:x_hi] = V[probe, x_lo:x_hi] < DARK_V
            refined = _dark_runs(band)
            if len(refined) < MIN_TRACKS:
                continue
            widths = np.array([b - a + 1 for a, b in refined])
            med = float(np.median(widths))
            refined = [r for r, wd in zip(refined, widths)
                       if abs(wd - med) <= max(4, med * 0.08)
                       and r[0] >= x_lo and r[1] <= x_hi]
            if len(refined) >= 2:
                refined.sort()
                runs = refined
                break
    else:
        top, bottom = row_y, row_y + 3

    (hx0, hx1), (ax0, ax1) = runs[0], runs[1]
    bar_h = max(3, bottom - top + 1)

    # The percentage text sits directly above the adrenaline track, starting a
    # few pixels to its left. Measured offsets on the live client: 5px left,
    # 17px up, 15px tall.
    text_x = max(0, ax0 - 5)
    text_y = max(0, top - 17)
    text_w = min(w - text_x, int((ax1 - ax0 + 1) * 0.62))
    text_h = max(8, top - text_y - 2)

    return Located(
        hp_bar=(hx0, top, hx1 - hx0 + 1, bar_h),
        adren_bar=(ax0, top, ax1 - ax0 + 1, bar_h),
        adren_text=(text_x, text_y, text_w, text_h),
        row_y=row_y,
        confidence=f"{len(runs)} matching tracks on row {row_y}",
    )
