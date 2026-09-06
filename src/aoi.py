"""World-anchored AOI fence — locked to the cyan V ground marker.

Place a cyan V tile/ground marker in the fight room. At mark time we store the
fence corners as offsets from that marker. Each bot tick we locate that V via
template match on the cyan mask inside ``search_rect`` (never generic cyan
UI/chat/minimap text), hold through brief OFF blinks, and rebuild the polygon
as marker_pos + saved offsets.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from pathlib import Path

from .window import GameWindow

DEFAULT_DEADZONE_FRAC = 0.055

# Sampled from your marker screenshots (HSV OpenCV scale).
DEFAULT_HSV_LOW = [88, 40, 100]
DEFAULT_HSV_HIGH = [125, 255, 255]
DEFAULT_MIN_AREA = 25
DEFAULT_MAX_AREA = 5000
DEFAULT_BLINK_PERIOD_S = 0.5
DEFAULT_HOLDOVER_S = 0.60
DEFAULT_ARM_WINDOW_S = 2.5
DEFAULT_LOST_AFTER_S = 1.2
DEFAULT_TEMPLATE_THRESHOLD = 0.62
MARKER_TEMPLATE_PATH = Path("reference/markers/cyan_marker_crop.png")


@dataclass(frozen=True)
class AOI:
    """Screen-space clickable region for one frame (after marker lock)."""

    anchor_x: int
    anchor_y: int
    polygon: np.ndarray
    deadzone_r: int
    bbox: tuple[int, int, int, int]
    match_score: float = 1.0
    fence_found: bool = True


@dataclass
class MarkerTracker:
    """Tracks the cyan ground marker across frames."""

    last_mx: int = 0
    last_my: int = 0
    last_seen_t: float = 0.0
    has_lock: bool = False
    last_score: float = 0.0
    armed: bool = False
    template: np.ndarray | None = None
    _was_present: bool | None = None
    _flips: deque = field(default_factory=lambda: deque(maxlen=12))

    def reset(self) -> None:
        self.has_lock = False
        self.armed = False
        self.last_score = 0.0
        self._was_present = None
        self._flips.clear()

    def ensure_template(self, mcfg: dict) -> None:
        """Load the cyan V crop once (ignores minimap cyan / UI dots)."""
        if self.template is not None:
            return
        path = Path(mcfg.get("template_path") or MARKER_TEMPLATE_PATH)
        if not path.exists():
            return
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is not None and img.size > 0:
            self.template = img


def anchor_point(win: GameWindow) -> tuple[int, int]:
    """Player screen position — centre of the game window."""
    return win.x + win.width // 2, win.y + win.height // 2


def deadzone_radius(win: GameWindow, deadzone_frac: float) -> int:
    return max(8, int(round(deadzone_frac * min(win.width, win.height))))


def _marker_cfg(aoi_cfg: dict) -> dict:
    m = dict(aoi_cfg.get("marker") or {})
    m.setdefault("hsv_low", DEFAULT_HSV_LOW)
    m.setdefault("hsv_high", DEFAULT_HSV_HIGH)
    m.setdefault("min_area", DEFAULT_MIN_AREA)
    m.setdefault("max_area", DEFAULT_MAX_AREA)
    m.setdefault("blink_period_s", DEFAULT_BLINK_PERIOD_S)
    m.setdefault("holdover_s", DEFAULT_HOLDOVER_S)
    m.setdefault("arm_window_s", DEFAULT_ARM_WINDOW_S)
    m.setdefault("lost_after_s", DEFAULT_LOST_AFTER_S)
    m.setdefault("template_threshold", DEFAULT_TEMPLATE_THRESHOLD)
    m.setdefault("template_scales", [0.5, 0.65, 0.8, 1.0, 1.2, 1.4])
    m.setdefault("default_search_frac",
                 list(aoi_cfg.get("default_search_frac")
                      or [0.05, 0.08, 0.68, 0.78]))
    # Optional playfield box as window fractions: [x1, y1, x2, y2]
    if aoi_cfg.get("search_rect"):
        m["search_rect"] = list(aoi_cfg["search_rect"])
    elif "search_rect" not in m:
        m["search_rect"] = None
    return m


def resolve_search_rect(mcfg: dict, fw: int, fh: int
                        ) -> tuple[int, int, int, int] | None:
    """Return pixel (x0,y0,x1,y1) clip box inside the game frame, or None."""
    raw = mcfg.get("search_rect")
    if not raw or len(raw) != 4:
        return None
    x1, y1, x2, y2 = (float(v) for v in raw)
    # Allow either fractions (0–1) or absolute window pixels.
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
        xa = int(round(min(x1, x2) * fw))
        ya = int(round(min(y1, y2) * fh))
        xb = int(round(max(x1, x2) * fw))
        yb = int(round(max(y1, y2) * fh))
    else:
        xa = int(round(min(x1, x2)))
        ya = int(round(min(y1, y2)))
        xb = int(round(max(x1, x2)))
        yb = int(round(max(y1, y2)))
    xa = max(0, min(fw - 1, xa))
    ya = max(0, min(fh - 1, ya))
    xb = max(xa + 8, min(fw, xb))
    yb = max(ya + 8, min(fh, yb))
    return xa, ya, xb, yb


def _in_ui_zone(cx: int, cy: int, w: int, h: int) -> bool:
    """True if point is on minimap / action bar / side panels (player-relative UI)."""
    if cx >= int(w * 0.68) and cy <= int(h * 0.42):
        return True
    if cy >= int(h * 0.78):
        return True
    if cx >= int(w * 0.88):
        return True
    if cy <= int(h * 0.07):
        return True
    if cx <= int(w * 0.02):
        return True
    return False


def cyan_mask(frame_bgr: np.ndarray, hsv_low: list[int], hsv_high: list[int],
              search_xyxy: tuple[int, int, int, int] | None = None
              ) -> np.ndarray:
    """Binary mask of cyan marker pixels; optional user search box."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    low = np.array(hsv_low, dtype=np.uint8)
    high = np.array(hsv_high, dtype=np.uint8)
    mask = cv2.inRange(hsv, low, high)
    h, w = mask.shape
    if search_xyxy is not None:
        x0, y0, x1, y1 = search_xyxy
        keep = np.zeros_like(mask)
        keep[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
        mask = keep
    else:
        # Default: wipe UI so we never lock the fence to the minimap.
        mask[: int(h * 0.07), :] = 0
        mask[int(h * 0.78) :, :] = 0
        mask[:, int(w * 0.88) :] = 0
        mask[: int(h * 0.42), int(w * 0.68) :] = 0
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    return mask


def find_marker_blobs(frame_bgr: np.ndarray, mcfg: dict
                      ) -> list[tuple[int, int, float, int]]:
    """Return candidate markers as (cx, cy, score, area), best first."""
    fh, fw = frame_bgr.shape[:2]
    search = resolve_search_rect(mcfg, fw, fh)
    mask = cyan_mask(frame_bgr, mcfg["hsv_low"], mcfg["hsv_high"], search)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_a = int(mcfg["min_area"])
    max_a = int(mcfg["max_area"])
    out: list[tuple[int, int, float, int]] = []
    for cnt in contours:
        area = int(cv2.contourArea(cnt))
        if area < min_a or area > max_a:
            continue
        m = cv2.moments(cnt)
        if m["m00"] < 1e-3:
            continue
        cx = int(round(m["m10"] / m["m00"]))
        cy = int(round(m["m01"] / m["m00"]))
        if search is None and _in_ui_zone(cx, cy, fw, fh):
            continue
        if search is not None:
            x0, y0, x1, y1 = search
            if not (x0 <= cx < x1 and y0 <= cy < y1):
                continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw < 4 or bh < 4:
            continue
        aspect = bw / float(bh)
        if aspect < 0.35 or aspect > 4.5:
            continue
        fill = area / float(max(1, bw * bh))
        size_score = 1.0 - abs(area - 200) / 2000.0
        score = max(0.05, size_score) * (0.4 + 0.6 * min(1.0, fill * 2))
        out.append((cx, cy, float(score), area))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def match_marker_template(frame_bgr: np.ndarray, template: np.ndarray,
                          threshold: float = DEFAULT_TEMPLATE_THRESHOLD,
                          search_xyxy: tuple[int, int, int, int] | None = None,
                          hsv_low: list[int] | None = None,
                          hsv_high: list[int] | None = None,
                          scales: list[float] | None = None
                          ) -> tuple[int, int, float] | None:
    """Find the cyan V template — shape+colour, not random cyan UI text.

    Matches on the cyan binary mask so chat/minimap text cannot win.
    Tries a few scales because zoom changes the V size slightly.
    """
    if template is None or template.size == 0:
        return None
    hsv_low = hsv_low or DEFAULT_HSV_LOW
    hsv_high = hsv_high or DEFAULT_HSV_HIGH
    scale_list = list(scales) if scales else [0.50, 0.65, 0.80, 1.0, 1.20, 1.40]
    fh, fw = frame_bgr.shape[:2]
    if search_xyxy is not None:
        x0, y0, x1, y1 = search_xyxy
    else:
        # Fallback playfield — prefer values from marker cfg when wired through.
        x0, y0 = int(fw * 0.05), int(fh * 0.08)
        x1, y1 = int(fw * 0.68), int(fh * 0.78)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None

    roi_bgr = frame_bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    roi_mask = cv2.inRange(hsv, np.array(hsv_low, dtype=np.uint8),
                           np.array(hsv_high, dtype=np.uint8))
    kernel = np.ones((3, 3), np.uint8)
    roi_mask = cv2.morphologyEx(roi_mask, cv2.MORPH_OPEN, kernel, iterations=1)

    tmpl_hsv = cv2.cvtColor(template, cv2.COLOR_BGR2HSV)
    tmpl_mask = cv2.inRange(tmpl_hsv, np.array(hsv_low, dtype=np.uint8),
                            np.array(hsv_high, dtype=np.uint8))
    # Require a real cyan V silhouette — never gray-match (that hits UI text).
    if int(tmpl_mask.sum()) < 30:
        return None
    tmpl_gray = tmpl_mask
    hay = roi_mask

    best: tuple[int, int, float] | None = None
    for scale in scale_list:
        tw = max(8, int(round(template.shape[1] * scale)))
        th = max(8, int(round(template.shape[0] * scale)))
        if hay.shape[0] < th or hay.shape[1] < tw:
            continue
        needle = cv2.resize(tmpl_gray, (tw, th), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(hay, needle, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        score = float(max_val)
        if best is None or score > best[2]:
            cx = x0 + max_loc[0] + tw // 2
            cy = y0 + max_loc[1] + th // 2
            best = (cx, cy, score)

    if best is None or best[2] < threshold:
        return None
    cx, cy, score = best
    if search_xyxy is None and _in_ui_zone(cx, cy, fw, fh):
        return None
    return cx, cy, score


def detect_marker(frame_bgr: np.ndarray, mcfg: dict,
                  near: tuple[int, int] | None = None,
                  near_px: int = 160,
                  template: np.ndarray | None = None
                  ) -> tuple[int, int, float] | None:
    """Locate the cyan V ground marker only (never generic cyan text/UI).

    When the V template is available, blob colour-matching is disabled — that
    path was locking onto minimap/chat cyan and made the fence follow the player.
    """
    fh, fw = frame_bgr.shape[:2]
    search = resolve_search_rect(mcfg, fw, fh)
    if search is None and mcfg.get("default_search_frac"):
        tmp = dict(mcfg)
        tmp["search_rect"] = mcfg["default_search_frac"]
        search = resolve_search_rect(tmp, fw, fh)
    tmpl_thr = float(mcfg.get("template_threshold", DEFAULT_TEMPLATE_THRESHOLD))

    if template is None and MARKER_TEMPLATE_PATH.exists():
        template = cv2.imread(str(MARKER_TEMPLATE_PATH), cv2.IMREAD_COLOR)

    if template is not None:
        hit = match_marker_template(
            frame_bgr, template, tmpl_thr, search,
            hsv_low=mcfg.get("hsv_low"), hsv_high=mcfg.get("hsv_high"),
            scales=mcfg.get("template_scales"))
        if hit is not None and near is not None:
            # Soft gate: if we have a prior lock, prefer hits near it unless score is excellent.
            nx, ny = near
            if (hit[0] - nx) ** 2 + (hit[1] - ny) ** 2 > near_px ** 2 and hit[2] < 0.85:
                # Re-search is still the same global best; accept it (camera jump).
                pass
        return hit

    # Never fall back to generic cyan blobs — those lock onto minimap/chat text.
    return None


def wait_for_marker(grab_fn, region: dict, mcfg: dict,
                    timeout_s: float = 1.6,
                    template: np.ndarray | None = None
                    ) -> tuple[int, int, float] | None:
    """Live-grab until the cyan marker is ON (blinks — may be off on freeze)."""
    if template is None and MARKER_TEMPLATE_PATH.exists():
        template = cv2.imread(str(MARKER_TEMPLATE_PATH), cv2.IMREAD_COLOR)
    deadline = time.monotonic() + timeout_s
    best = None
    while time.monotonic() < deadline:
        frame = grab_fn(region)
        hit = detect_marker(frame, mcfg, template=template)
        if hit is not None:
            if best is None or hit[2] > best[2]:
                best = hit
            if hit[2] >= 0.25:
                return hit
        time.sleep(0.05)
    return best


def offsets_from_marker(area_pts: list[tuple[int, int]],
                        mx: int, my: int,
                        win_w: int, win_h: int) -> list[list[float]]:
    """Fence corners as fractions of window size relative to marker."""
    return [
        [round((x - mx) / win_w, 5), round((y - my) / win_h, 5)]
        for x, y in area_pts
    ]


def polygon_from_marker(win: GameWindow, offsets: list[list[float]],
                        mx: int, my: int) -> np.ndarray:
    """Screen-absolute polygon from marker + relative offsets."""
    pts = []
    for ox, oy in offsets:
        pts.append([
            int(round(win.x + mx + ox * win.width)),
            int(round(win.y + my + oy * win.height)),
        ])
    return np.array(pts, dtype=np.int32)


def polygon_screen(win: GameWindow, fractions: list[list[float]]) -> np.ndarray:
    """Legacy absolute window-fraction polygon (mark.py preview seed only)."""
    pts = []
    for fx, fy in fractions:
        pts.append([int(round(win.x + fx * win.width)),
                    int(round(win.y + fy * win.height))])
    return np.array(pts, dtype=np.int32)


def normalize_polygon(vertices: list[list[float]]) -> list[list[float]]:
    """Pass-through for legacy absolute fractions (mark.py seed)."""
    return [[float(a), float(b)] for a, b in vertices]


def _aoi_from_poly(win: GameWindow, poly: np.ndarray,
                   ax: int, ay: int, radius: int,
                   score: float, found: bool,
                   clip_xyxy: tuple[int, int, int, int] | None = None
                   ) -> AOI | None:
    """Build AOI from a screen-absolute polygon.

    ``clip_xyxy`` is an optional screen-absolute (x0,y0,x1,y1) box — normally
    the ``v`` search rect. The AOI crop is the polygon's bounding box
    *intersected* with that box so action-bar / minimap never appear in the
    RS3 AOI window just because a fence corner sat low.
    """
    x, y, w, h = cv2.boundingRect(poly)
    x0 = max(win.x, x)
    y0 = max(win.y, y)
    x1 = min(win.x + win.width, x + w)
    y1 = min(win.y + win.height, y + h)
    if clip_xyxy is not None:
        cx0, cy0, cx1, cy1 = clip_xyxy
        x0 = max(x0, cx0)
        y0 = max(y0, cy0)
        x1 = min(x1, cx1)
        y1 = min(y1, cy1)
    if x1 <= x0 or y1 <= y0:
        return None
    return AOI(anchor_x=ax, anchor_y=ay, polygon=poly, deadzone_r=radius,
               bbox=(x0, y0, x1 - x0, y1 - y0),
               match_score=score, fence_found=found)


# Back-compat alias used by older bot wiring.
FenceTracker = MarkerTracker


def locate_fence(win: GameWindow, cfg: dict, frame_bgr: np.ndarray,
                 tracker: MarkerTracker) -> AOI | None:
    """Rebuild the fence from the blinking cyan marker this frame."""
    aoi_cfg = cfg.get("aoi") or {}
    offsets = aoi_cfg.get("polygon_offset") or []
    if len(offsets) < 3:
        tracker.last_score = 0.0
        tracker.has_lock = False
        return None

    mcfg = _marker_cfg(aoi_cfg)
    tracker.ensure_template(mcfg)
    dz_frac = float(aoi_cfg.get("deadzone_frac", DEFAULT_DEADZONE_FRAC))
    ax, ay = anchor_point(win)
    radius = deadzone_radius(win, dz_frac)
    now = time.monotonic()

    near = (tracker.last_mx, tracker.last_my) if tracker.has_lock else None
    hit = detect_marker(frame_bgr, mcfg, near=near, template=tracker.template)
    present = hit is not None

    # Track blinks for diagnostics only — do NOT gate lock on them.
    # Static HUD cyan kept present=True forever so armed never flipped.
    if tracker._was_present is not None and present != tracker._was_present:
        tracker._flips.append(now)
    tracker._was_present = present
    arm_window = float(mcfg["arm_window_s"])
    while tracker._flips and now - tracker._flips[0] > arm_window:
        tracker._flips.popleft()
    if len(tracker._flips) >= 1:
        tracker.armed = True

    holdover = float(mcfg["holdover_s"])
    lost_after = float(mcfg["lost_after_s"])

    mx = my = 0
    score = 0.0

    if present:
        mx, my, score = hit  # type: ignore[misc]
        tracker.last_mx, tracker.last_my = mx, my
        tracker.last_seen_t = now
        tracker.last_score = score
        tracker.armed = True  # solid sight is enough to lock
    elif tracker.last_seen_t > 0 and (now - tracker.last_seen_t) <= holdover:
        mx, my = tracker.last_mx, tracker.last_my
        score = tracker.last_score * 0.85
    else:
        tracker.has_lock = False
        tracker.last_score = score
        return None

    if tracker.last_seen_t > 0 and (now - tracker.last_seen_t) > lost_after and not present:
        tracker.has_lock = False
        return None

    tracker.has_lock = True
    tracker.last_score = score
    poly = polygon_from_marker(win, offsets, mx, my)
    # Clip AOI crop to the `v` search box so HUD below/outside never shows
    # up in the RS3 AOI window (search_rect ≠ fence, but must bound the crop).
    clip_screen = None
    search = resolve_search_rect(mcfg, win.width, win.height)
    if search is not None:
        sx0, sy0, sx1, sy1 = search
        clip_screen = (win.x + sx0, win.y + sy0, win.x + sx1, win.y + sy1)
    return _aoi_from_poly(win, poly, ax, ay, radius, score=score, found=True,
                          clip_xyxy=clip_screen)


def build(win: GameWindow, cfg: dict) -> AOI | None:
    """Static preview without live marker (offsets ignored — mark-time only)."""
    aoi_cfg = cfg.get("aoi") or {}
    fracs = aoi_cfg.get("polygon") or []
    if len(fracs) < 3:
        return None
    dz_frac = float(aoi_cfg.get("deadzone_frac", DEFAULT_DEADZONE_FRAC))
    poly = polygon_screen(win, fracs)
    ax, ay = anchor_point(win)
    return _aoi_from_poly(win, poly, ax, ay, deadzone_radius(win, dz_frac),
                          score=0.0, found=True)


def point_in_polygon(x: int, y: int, polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon, (float(x), float(y)), False) >= 0


def point_in_deadzone(x: int, y: int, ax: int, ay: int, radius: int) -> bool:
    return (x - ax) ** 2 + (y - ay) ** 2 <= radius ** 2


def is_valid_click(x: int, y: int, aoi: AOI,
                   clip_xyxy: tuple[int, int, int, int] | None = None) -> bool:
    if point_in_deadzone(x, y, aoi.anchor_x, aoi.anchor_y, aoi.deadzone_r):
        return False
    if clip_xyxy is not None:
        x0, y0, x1, y1 = clip_xyxy
        if not (x0 <= x < x1 and y0 <= y < y1):
            return False
    return point_in_polygon(x, y, aoi.polygon)


def draw_overlay(frame_bgr: np.ndarray, aoi: AOI,
                 blobs: list[tuple[int, int]],
                 pick: tuple[int, int] | None,
                 origin: tuple[int, int],
                 status: str | None = None,
                 marker_xy: tuple[int, int] | None = None) -> np.ndarray:
    """Draw marker-locked fence, dead-zone, blobs, and pick onto a crop."""
    out = frame_bgr.copy()
    ox, oy = origin

    def local(pt):
        return int(pt[0] - ox), int(pt[1] - oy)

    local_poly = np.array([local(p) for p in aoi.polygon], dtype=np.int32)
    cv2.polylines(out, [local_poly], True, (80, 220, 80), 2)

    ax, ay = local((aoi.anchor_x, aoi.anchor_y))
    if 0 <= ax < out.shape[1] and 0 <= ay < out.shape[0]:
        cv2.circle(out, (ax, ay), aoi.deadzone_r, (60, 60, 255), 2)
        cv2.circle(out, (ax, ay), 3, (60, 60, 255), -1)

    if marker_xy is not None:
        cv2.drawMarker(out, local(marker_xy), (255, 220, 0),
                       cv2.MARKER_TILTED_CROSS, 18, 2)

    for bx, by in blobs:
        cv2.circle(out, local((bx, by)), 6, (255, 220, 0), 2)
    if pick is not None:
        cv2.circle(out, local(pick), 10, (0, 255, 255), 2)
        cv2.drawMarker(out, local(pick), (0, 255, 255),
                       cv2.MARKER_CROSS, 16, 2)

    label = status or f"marker {aoi.match_score:.2f}"
    cv2.putText(out, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (220, 220, 220), 1)
    return out
