"""Free pixel click — mark.py colours on the visible game view.

No cyan-marker fence / AOI crop. Still requires:
* blob stillness (same ``static_still_s`` as Method → static)
* target-bar arm / hold / retarget cooldown (wired in the bot tick)

Used only when ``behavior.attack_method`` is ``free``.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import aoi as aoi_mod
from .targets import Target, largest, nearest, zombie_mask

DEFAULT_CLICK_COOLDOWN_S = 1.6


@dataclass
class FreePixelState:
    """Cooldown bookkeeping for free-pixel clicks."""

    last_click_at: float = -1e9

    def reset(self) -> None:
        self.last_click_at = -1e9


def _visible_mask(shape: tuple[int, int], cfg: dict | None) -> np.ndarray:
    """True on the playfield: search box if set, else full frame minus HUD."""
    h, w = shape
    aoi_cfg = (cfg or {}).get("aoi") or {}
    mcfg = aoi_mod._marker_cfg(aoi_cfg)
    search = aoi_mod.resolve_search_rect(mcfg, w, h)
    keep = np.zeros((h, w), dtype=bool)
    if search is not None:
        x0, y0, x1, y1 = search
        keep[y0:y1, x0:x1] = True
        return keep
    # No search box — whole window except typical UI chrome.
    keep[:, :] = True
    keep[: int(h * 0.07), :] = False
    keep[int(h * 0.78) :, :] = False
    keep[:, int(w * 0.88) :] = False
    keep[: int(h * 0.42), int(w * 0.68) :] = False
    return keep


def find_visible_targets(
    frame_bgr: np.ndarray,
    win,
    cfg: dict | None = None,
) -> list[Target]:
    """Detect colour blobs on the visible playfield (absolute screen coords).

    Args:
        frame_bgr: Game-window capture (window-local pixels).
        win: Game window with ``x``, ``y``, ``width``, ``height``.
        cfg: Bot config (uses ``targeting.*`` colours from mark.py).

    Returns:
        Targets in absolute screen coordinates, densest-first friendly.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    tcfg = (cfg or {}).get("targeting") or {}
    if not (tcfg.get("zombie_colors_bgr") or []):
        return []

    h, w = frame_bgr.shape[:2]
    min_area = int(tcfg.get("min_blob_area", 180))
    max_area = int(tcfg.get("max_blob_area", 25000))
    rel_frac = float(tcfg.get("relative_area_frac", 0.35))

    mask = zombie_mask(frame_bgr, cfg) & _visible_mask((h, w), cfg)
    kernel = np.ones((3, 3), np.uint8)
    cleaned = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel)

    n, _labels, stats, centroids = cv2.connectedComponentsWithStats(cleaned, 8)
    ox, oy = int(win.x), int(win.y)
    ax = ox + int(win.width) // 2
    ay = oy + int(win.height) // 2
    dz = aoi_mod.deadzone_radius(
        win, float((cfg or {}).get("aoi", {}).get("deadzone_frac", 0.055)))

    found: list[Target] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        cx = int(round(centroids[i][0])) + ox
        cy = int(round(centroids[i][1])) + oy
        if aoi_mod.point_in_deadzone(cx, cy, ax, ay, dz):
            continue
        found.append(Target(x=cx, y=cy, area=area))

    if len(found) >= 2 and rel_frac > 0:
        biggest = max(t.area for t in found)
        floor = int(biggest * rel_frac)
        found = [t for t in found if t.area >= floor]
    return found


def pick_target(found: list[Target], win, cfg: dict | None = None) -> Target | None:
    """Choose nearest-to-center blob (or densest if ``click_nearest`` is false)."""
    tcfg = (cfg or {}).get("targeting") or {}
    if tcfg.get("click_nearest", True):
        ax = int(win.x) + int(win.width) // 2
        ay = int(win.y) + int(win.height) // 2
        return nearest(found, ax, ay)
    return largest(found)


def draw_preview(frame_bgr: np.ndarray, win, found: list[Target],
                 pick: Target | None, status: str) -> np.ndarray:
    """Simple preview of colour hits on the full frame (scaled later by caller)."""
    out = frame_bgr.copy()
    ox, oy = int(win.x), int(win.y)
    for t in found:
        cv2.circle(out, (t.x - ox, t.y - oy), 8, (0, 220, 255), 2)
    if pick is not None:
        cv2.drawMarker(out, (pick.x - ox, pick.y - oy), (0, 255, 255),
                       cv2.MARKER_CROSS, 18, 2)
    cv2.putText(out, status, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (220, 220, 220), 1)
    # Outline visible playfield (search box) when configured.
    return out
