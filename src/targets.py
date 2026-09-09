"""Pixel-based zombie detection inside the AOI.

Colours come from YOU: run `python3 mark.py`, press z, left-click zombies,
right-click bones/floor to exclude. Samples live in config.json under
targeting.zombie_colors_bgr / zombie_exclude_bgr.

Clicks prefer the densest (largest) colour blob so enemies win over tiny loot.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import aoi as aoi_mod

_MIN_BLOB_AREA = 180
_MAX_BLOB_AREA = 25000
_DEFAULT_TOLERANCE = 10
_DEFAULT_EXCLUDE_TOLERANCE = 12
_RELATIVE_AREA_FRAC = 0.35


@dataclass(frozen=True)
class Target:
    """One detected enemy blob in absolute screen coordinates."""

    x: int
    y: int
    area: int


def mask_from_colors(img_bgr: np.ndarray, colors_bgr: list[list[int]] | list[tuple],
                     tolerance: int = _DEFAULT_TOLERANCE) -> np.ndarray:
    """True where every BGR channel is within `tolerance` of a sample colour.

    Per-channel max-abs (Chebyshev) matching — much tighter than summing
    channels, so dark greys do not light up the whole dungeon floor.
    """
    if img_bgr.size == 0 or not colors_bgr:
        return np.zeros(img_bgr.shape[:2], dtype=bool)

    img = img_bgr.astype(np.int16)
    hit = np.zeros(img_bgr.shape[:2], dtype=bool)
    for c in colors_bgr:
        b, g, r = int(c[0]), int(c[1]), int(c[2])
        db = np.abs(img[:, :, 0] - b)
        dg = np.abs(img[:, :, 1] - g)
        dr = np.abs(img[:, :, 2] - r)
        hit |= (db <= tolerance) & (dg <= tolerance) & (dr <= tolerance)
    return hit


def zombie_mask(img_bgr: np.ndarray, cfg: dict | None = None) -> np.ndarray:
    """Boolean mask of zombie-like pixels using colours from config (or empty)."""
    tcfg = (cfg or {}).get("targeting") or {}
    colors = tcfg.get("zombie_colors_bgr") or []
    if not colors:
        return np.zeros(img_bgr.shape[:2], dtype=bool)
    tol = int(tcfg.get("color_tolerance", _DEFAULT_TOLERANCE))
    hit = mask_from_colors(img_bgr, colors, tol)
    exclude = tcfg.get("zombie_exclude_bgr") or []
    if exclude:
        etol = int(tcfg.get("exclude_tolerance", _DEFAULT_EXCLUDE_TOLERANCE))
        hit &= ~mask_from_colors(img_bgr, exclude, etol)
    return hit


def _polygon_mask(shape: tuple[int, int], polygon_local: np.ndarray) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    if len(polygon_local) >= 3:
        cv2.fillPoly(mask, [polygon_local], 255)
    return mask > 0


def find_zombies(frame_bgr: np.ndarray, aoi: aoi_mod.AOI,
                 origin: tuple[int, int],
                 cfg: dict | None = None,
                 min_area: int | None = None,
                 max_area: int | None = None) -> list[Target]:
    """Detect zombie blobs in a crop whose top-left screen position is `origin`.

    Uses user-picked colours from config. Blobs inside the player dead-zone are
    discarded. After detection, drops blobs smaller than
    ``relative_area_frac`` of the largest hit so loot cannot compete with
    enemies. Returns absolute screen coordinates.
    """
    tcfg = (cfg or {}).get("targeting") or {}
    if min_area is None:
        min_area = int(tcfg.get("min_blob_area", _MIN_BLOB_AREA))
    if max_area is None:
        max_area = int(tcfg.get("max_blob_area", _MAX_BLOB_AREA))
    rel_frac = float(tcfg.get("relative_area_frac", _RELATIVE_AREA_FRAC))

    ox, oy = origin
    h, w = frame_bgr.shape[:2]
    local_poly = np.array(
        [[int(p[0] - ox), int(p[1] - oy)] for p in aoi.polygon], dtype=np.int32)
    region = _polygon_mask((h, w), local_poly)
    mask = zombie_mask(frame_bgr, cfg) & region

    kernel = np.ones((3, 3), np.uint8)
    cleaned = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel)

    n, labels, stats, centroids = cv2.connectedComponentsWithStats(cleaned, 8)
    targets: list[Target] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        cx = int(round(centroids[i][0])) + ox
        cy = int(round(centroids[i][1])) + oy
        if aoi_mod.point_in_deadzone(cx, cy, aoi.anchor_x, aoi.anchor_y,
                                     aoi.deadzone_r):
            continue
        if not aoi_mod.point_in_polygon(cx, cy, aoi.polygon):
            continue
        targets.append(Target(x=cx, y=cy, area=area))

    if len(targets) >= 2 and rel_frac > 0:
        biggest = max(t.area for t in targets)
        floor = int(biggest * rel_frac)
        targets = [t for t in targets if t.area >= floor]
    return targets


def nearest(targets: list[Target], ax: int, ay: int) -> Target | None:
    """The target closest to the player anchor, or None if the list is empty."""
    if not targets:
        return None
    return min(targets, key=lambda t: (t.x - ax) ** 2 + (t.y - ay) ** 2)


def largest(targets: list[Target]) -> Target | None:
    """The densest colour blob (largest area) — prefers enemies over loot."""
    if not targets:
        return None
    return max(targets, key=lambda t: t.area)


def zombie_colour_under(
    frame_bgr: np.ndarray,
    x: int,
    y: int,
    cfg: dict | None = None,
    *,
    radius_px: int = 2,
) -> bool:
    """True if a zombie colour is still under/near ``(x, y)`` in the frame.

    ``(x, y)`` are frame-local pixel coords (window-relative). Checks a small
    square around the point so a 1–2px aim slip does not false-abort. Used as a
    last-moment pre-click verify after the mouse has moved.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return False
    h, w = frame_bgr.shape[:2]
    if w < 1 or h < 1:
        return False
    r = max(0, int(radius_px))
    x0 = max(0, int(x) - r)
    y0 = max(0, int(y) - r)
    x1 = min(w, int(x) + r + 1)
    y1 = min(h, int(y) + r + 1)
    if x0 >= x1 or y0 >= y1:
        return False
    patch = frame_bgr[y0:y1, x0:x1]
    return bool(np.any(zombie_mask(patch, cfg)))


def snap_aim_to_colour(
    frame_bgr: np.ndarray,
    x: int,
    y: int,
    cfg: dict | None = None,
) -> tuple[int, int, int] | None:
    """Refine aim to the colour centroid in a small patch (frame-local).

    Between cyan-trap and the final pixel check: require
    ``targeting.pre_click_min_match_px`` matching pixels in
    ``pre_click_snap_patch_px``, then nudge toward the match centroid by at most
    ``pre_click_max_snap_px``.

    Returns:
        ``(nx, ny, match_count)`` frame-local, or ``None`` if too few matches
        / snap disabled with no colour under the point.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    h, w = frame_bgr.shape[:2]
    if w < 1 or h < 1:
        return None
    tcfg = (cfg or {}).get("targeting") or {}
    if not bool(tcfg.get("pre_click_snap_enabled", True)):
        # Snap off — still require at least one match near the tip.
        if zombie_colour_under(
            frame_bgr, x, y, cfg,
            radius_px=int(tcfg.get("pre_click_verify_radius_px", 3)),
        ):
            return int(x), int(y), 1
        return None

    patch_sz = max(3, int(tcfg.get("pre_click_snap_patch_px", 11)))
    if patch_sz % 2 == 0:
        patch_sz += 1
    half = patch_sz // 2
    min_match = max(1, int(tcfg.get("pre_click_min_match_px", 12)))
    max_snap = max(0, int(tcfg.get("pre_click_max_snap_px", 3)))

    x0 = max(0, int(x) - half)
    y0 = max(0, int(y) - half)
    x1 = min(w, int(x) + half + 1)
    y1 = min(h, int(y) + half + 1)
    if x0 >= x1 or y0 >= y1:
        return None
    patch = frame_bgr[y0:y1, x0:x1]
    mask = zombie_mask(patch, cfg)
    count = int(np.count_nonzero(mask))
    if count < min_match:
        return None

    ys, xs = np.where(mask)
    # Mean centroid in frame coords.
    cx = float(np.mean(xs)) + x0
    cy = float(np.mean(ys)) + y0
    dx = cx - float(x)
    dy = cy - float(y)
    dist = (dx * dx + dy * dy) ** 0.5
    if dist > max_snap > 0:
        scale = max_snap / dist
        cx = float(x) + dx * scale
        cy = float(y) + dy * scale
    elif max_snap == 0:
        cx, cy = float(x), float(y)

    nx = int(round(cx))
    ny = int(round(cy))
    nx = max(0, min(w - 1, nx))
    ny = max(0, min(h - 1, ny))
    return nx, ny, count


# Bright trap cyan — saturated enough to skip grey rock; loose on hue.
_DEFAULT_CYAN_HSV_LOW = (88, 90, 80)
_DEFAULT_CYAN_HSV_HIGH = (110, 255, 255)


def cyan_near(
    frame_bgr: np.ndarray,
    x: int,
    y: int,
    cfg: dict | None = None,
) -> bool:
    """True if bright cyan trap colour is inside a square around ``(x, y)``.

    ``(x, y)`` are frame-local. Window side is ``targeting.cyan_avoid_size_px``
    (default 100 → 100×100). Gated by ``cyan_avoid_enabled`` in the click path.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return False
    h, w = frame_bgr.shape[:2]
    if w < 1 or h < 1:
        return False
    tcfg = (cfg or {}).get("targeting") or {}
    size = max(8, int(tcfg.get("cyan_avoid_size_px", 100)))
    half = size // 2
    min_px = max(1, int(tcfg.get("cyan_avoid_min_pixels", 60)))
    raw_lo = tcfg.get("cyan_avoid_hsv_low") or list(_DEFAULT_CYAN_HSV_LOW)
    raw_hi = tcfg.get("cyan_avoid_hsv_high") or list(_DEFAULT_CYAN_HSV_HIGH)
    lo = np.array([int(raw_lo[0]), int(raw_lo[1]), int(raw_lo[2])],
                  dtype=np.uint8)
    hi = np.array([int(raw_hi[0]), int(raw_hi[1]), int(raw_hi[2])],
                  dtype=np.uint8)

    x0 = max(0, int(x) - half)
    y0 = max(0, int(y) - half)
    x1 = min(w, int(x) + half)
    y1 = min(h, int(y) + half)
    if x0 >= x1 or y0 >= y1:
        return False
    patch = frame_bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lo, hi)
    return int(cv2.countNonZero(mask)) >= min_px
