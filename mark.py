#!/usr/bin/env python3
"""Mark your fight-area fence and zombie colours yourself.

    python3 mark.py

Takes a frozen screenshot of RuneScape so clicking is easy (nothing moves).

The green fence is WORLD-LOCKED to your cyan V ground marker (template match
on the V shape — not random cyan UI/chat/minimap text).

=============================================================================
MANDATORY SETUP ORDER (every new room / after zoom changes):
  0. In RS3: place a cyan V ground marker on the fight-room floor (not on you).
  1. Run mark.py — wait for freeze when the V blinks ON.
  2. Press v — SEARCH BOX: click 2 opposite corners around the playfield.
     Keep minimap, action bar, and chat OUTSIDE the orange box.
  3. Press a — AREA: left-click 3+ fence corners around the kill zone.
  4. Press z — ZOMBIE: left-click zombie body pixels; right-click bones/floor
     to exclude. Magenta tint must sit on zombies only.
  5. Press s — SAVE (blocked until search box + fence + colours + V are set).
=============================================================================

Modes (press the key):
  a   AREA mode  — edit fence corners
  z   ZOMBIE mode — sample enemy / exclude colours
  v   SEARCH BOX — click two opposite corners; marker is only sought inside
  h   HEALTH probe — one click on red bar at your eat threshold
  d   ADRENALINE probe — one click on empty adren bar (0%)
  t   TARGET-BAR probe — one click on top target bar while engaged

AREA mouse:
  left-click empty      add corner (or insert on nearest edge if insert ON)
  left-click a corner   pick it up; left-click again to place
  right-click           delete nearest corner

SEARCH BOX mouse:
  left-click            set corner 1, then corner 2 (x1,y1)–(x2,y2)
  right-click           clear search box

ZOMBIE mouse:
  left-click            sample zombie colour (include)
  right-click           sample bone/floor colour (exclude from match)

PROBE mouse (h / d / t):
  left-click            set / replace that probe (colour sampled now)
  right-click           clear that probe

Other keys:
  i   toggle INSERT-on-edge (area mode)
  u   undo last add (area corner / include / exclude / probe)
  c   clear current mode only
  x   clear room lock + zombie colours (probes are kept)
  r   refresh (5s countdown, then freeze on cyan marker ON)
  s   save to config.json and quit
  q   quit without saving
  [ ] dead-zone size   +/- colour tolerance

Probes persist across mark.py runs — only re-click what you want to change.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from src import aoi, capture, config_util, probes, targets, window

CONFIG_PATH = Path("config.json")
MAX_PREVIEW_W = 1280
BANNER_H = 154
FREEZE_COUNTDOWN_S = 5
MARKER_CATCH_TIMEOUT_S = 2.0
NEAR_PT_PX = 14
DEFAULT_COLOR_TOL = 10
MAX_COLOR_TOL = 16

# mark mode → (config key, draw BGR, short label)
PROBE_MODES = {
    "health": ("health", (60, 60, 255), "HP"),
    "adren": ("adrenaline", (40, 200, 255), "ADREN"),
    "tbar": ("target_bar", (0, 220, 255), "TBAR"),
}


def _probe_from_dict(entry: object) -> dict | None:
    """Normalize a saved probe entry, or None if unusable."""
    if not isinstance(entry, dict):
        return None
    xy = entry.get("xy_frac")
    bgr = entry.get("bgr")
    if not (isinstance(xy, (list, tuple)) and len(xy) >= 2):
        return None
    if not (isinstance(bgr, (list, tuple)) and len(bgr) >= 3):
        return None
    return {
        "xy_frac": [float(xy[0]), float(xy[1])],
        "bgr": [int(bgr[0]), int(bgr[1]), int(bgr[2])],
    }


def _load_saved_probes(cfg: dict) -> dict[str, dict]:
    """Probes from the last save (config.json / merged cfg)."""
    out: dict[str, dict] = {}
    # Prefer raw config.json so defaults' nulls cannot blank a live save.
    raw: dict = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
    pcfg = raw.get("probes") if isinstance(raw.get("probes"), dict) else None
    if not pcfg:
        pcfg = cfg.get("probes") if isinstance(cfg.get("probes"), dict) else {}
    for name in ("health", "adrenaline", "target_bar"):
        entry = _probe_from_dict(pcfg.get(name))
        if entry is not None:
            out[name] = entry
    return out


def load_cfg() -> dict:
    cfg = config_util.load_merged()
    if cfg is None:
        raise FileNotFoundError("No config.json or config.default.json found.")
    return cfg



def _show_countdown_tile(seconds_left: int, title: str = "Switch to RuneScape") -> None:
    tile = np.full((160, 520, 3), (28, 26, 24), np.uint8)
    cv2.putText(tile, title, (24, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (210, 210, 210), 2)
    cv2.putText(tile, f"Freezing in {seconds_left}…", (24, 110),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (80, 220, 80), 2)
    cv2.imshow("RS3 mark", tile)
    cv2.waitKey(1)


def freeze_game(grab: capture.Grabber, wait_s: int = FREEZE_COUNTDOWN_S,
                catch_marker: bool = True
                ) -> tuple[object, np.ndarray, tuple[int, int, float] | None]:
    """Countdown → re-find window → grab until cyan marker is ON."""
    print(f"Switch back to RuneScape — freezing in {wait_s} seconds…")
    for left in range(wait_s, 0, -1):
        print(f"  {left}…")
        _show_countdown_tile(left)
        time.sleep(1)

    win = window.find_game()
    if win is None:
        raise RuntimeError("RuneScape is not open anymore.")

    mcfg = aoi._marker_cfg({})
    tmpl = None
    if aoi.MARKER_TEMPLATE_PATH.exists():
        tmpl = cv2.imread(str(aoi.MARKER_TEMPLATE_PATH), cv2.IMREAD_COLOR)
    freeze = grab.grab(win.region)
    mhit = aoi.detect_marker(freeze, mcfg, template=tmpl) if catch_marker else None

    if catch_marker and mhit is None:
        print(f"Cyan marker OFF on first frame — catching blink (up to "
              f"{MARKER_CATCH_TIMEOUT_S:.1f}s)…")
        tile = np.full((160, 520, 3), (28, 26, 24), np.uint8)
        cv2.putText(tile, "Waiting for cyan blink ON…", (24, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 220, 0), 2)
        cv2.imshow("RS3 mark", tile)
        cv2.waitKey(1)
        deadline = time.monotonic() + MARKER_CATCH_TIMEOUT_S
        best = None
        while time.monotonic() < deadline:
            frame = grab.grab(win.region)
            hit = aoi.detect_marker(frame, mcfg, template=tmpl)
            if hit is not None:
                freeze = frame
                mhit = hit
                if best is None or hit[2] > best[2]:
                    best = hit
                if hit[2] >= 0.25:
                    break
            time.sleep(0.04)
        if mhit is None and best is not None:
            mhit = best

    if mhit is not None:
        print(f"Frozen {win.width}x{win.height} — cyan marker ON at "
              f"({mhit[0]},{mhit[1]}) score={mhit[2]:.2f}")
    else:
        print(f"Frozen {win.width}x{win.height} — cyan marker not seen "
              f"(press r again when it blinks ON).")
    return win, freeze, mhit


def sample_pixel_bgr(img: np.ndarray, x: int, y: int) -> list[int] | None:
    """Exact BGR at one pixel."""
    h, w = img.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return None
    b, g, r = (int(v) for v in img[y, x])
    return [b, g, r]


def nearest_index(pts: list[tuple[int, int]], x: int, y: int,
                  max_dist: float = NEAR_PT_PX) -> int | None:
    if not pts:
        return None
    best_i, best_d = None, max_dist * max_dist
    for i, (px, py) in enumerate(pts):
        d = (px - x) ** 2 + (py - y) ** 2
        if d <= best_d:
            best_d = d
            best_i = i
    return best_i


def nearest_edge_insert(pts: list[tuple[int, int]], x: int, y: int
                        ) -> tuple[int, tuple[int, int]]:
    """Return (insert_index, point) for the closest polygon edge."""
    n = len(pts)
    best_i, best_d, best_pt = 0, 1e18, (x, y)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        vx, vy = x1 - x0, y1 - y0
        seg = vx * vx + vy * vy
        if seg < 1:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy) / seg))
        qx, qy = int(round(x0 + t * vx)), int(round(y0 + t * vy))
        d = (qx - x) ** 2 + (qy - y) ** 2
        if d < best_d:
            best_d = d
            best_i = i + 1
            best_pt = (qx, qy)
    return best_i, best_pt


def dedupe_colors(colors: list[list[int]], min_delta: int = 6) -> list[list[int]]:
    uniq: list[list[int]] = []
    for c in colors:
        if not any(sum(abs(c[i] - u[i]) for i in range(3)) < min_delta for u in uniq):
            uniq.append(c)
    return uniq


class Marker:
    """Click handler for fence corners, search box, probes, and colour samples."""

    def __init__(self) -> None:
        self.mode = "area"  # area | zombie | search | health | adren | tbar
        self.area_pts: list[tuple[int, int]] = []
        self.color_pts: list[tuple[int, int]] = []
        self.exclude_pts: list[tuple[int, int]] = []
        self.search_corners: list[tuple[int, int]] = []  # up to 2 clicks
        # Probe entries keyed by config name: health / adrenaline / target_bar
        self.probe_entries: dict[str, dict] = {}
        self.freeze: np.ndarray | None = None
        self.win_w = 1
        self.win_h = 1
        self.scale = 1.0
        self.banner_h = BANNER_H
        self.insert_mode = False
        self.drag_idx: int | None = None  # fence corner being moved
        self.undo_stack: list[tuple] = []
        self.room_dirty = False
        self.zombie_dirty = False

    def search_rect_px(self) -> tuple[int, int, int, int] | None:
        if len(self.search_corners) < 2:
            return None
        (x1, y1), (x2, y2) = self.search_corners[0], self.search_corners[1]
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

    def search_rect_frac(self, win_w: int, win_h: int) -> list[float] | None:
        box = self.search_rect_px()
        if box is None:
            return None
        x0, y0, x1, y1 = box
        return [
            round(x0 / win_w, 4), round(y0 / win_h, 4),
            round(x1 / win_w, 4), round(y1 / win_h, 4),
        ]

    def to_game(self, x: int, y: int) -> tuple[int, int] | None:
        y -= self.banner_h
        if y < 0:
            return None
        gx = int(round(x / self.scale))
        gy = int(round(y / self.scale))
        return gx, gy

    def on_mouse(self, event, x, y, flags, param) -> None:
        pt = self.to_game(x, y)
        if pt is None:
            return
        gx, gy = pt

        if self.mode == "area":
            self._on_area_mouse(event, gx, gy)
        elif self.mode == "search":
            self._on_search_mouse(event, gx, gy)
        elif self.mode in PROBE_MODES:
            self._on_probe_mouse(event, gx, gy)
        else:
            self._on_zombie_mouse(event, gx, gy)

    def _on_probe_mouse(self, event, gx: int, gy: int) -> None:
        cfg_key, _col, label = PROBE_MODES[self.mode]
        if event == cv2.EVENT_RBUTTONDOWN:
            if cfg_key in self.probe_entries:
                old = self.probe_entries.pop(cfg_key)
                self.undo_stack.append(("probe_clear", cfg_key, old))
                print(f"  cleared {label} probe")
            return
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        if self.freeze is None:
            print("  no freeze frame — press r")
            return
        bgr = probes.sample_bgr(self.freeze, gx, gy)
        if bgr is None:
            print("  click was outside the image")
            return
        prev = self.probe_entries.get(cfg_key)
        entry = probes.make_entry(gx, gy, bgr, self.win_w, self.win_h)
        self.probe_entries[cfg_key] = entry
        self.undo_stack.append(("probe_set", cfg_key, prev))
        print(f"  {label} probe @ ({gx},{gy}) BGR={bgr}  "
              f"frac={entry['xy_frac']}")

    def _on_search_mouse(self, event, gx: int, gy: int) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            if len(self.search_corners) >= 2:
                self.search_corners = []
            self.search_corners.append((gx, gy))
            if len(self.search_corners) == 1:
                print(f"  search corner 1 at {(gx, gy)} — click opposite corner")
            else:
                box = self.search_rect_px()
                self.room_dirty = True
                print(f"  search box {box}  (marker only sought inside)")
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.search_corners = []
            self.room_dirty = True
            print("  cleared search box")

    def _on_area_mouse(self, event, gx: int, gy: int) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            if self.drag_idx is not None:
                self.area_pts[self.drag_idx] = (gx, gy)
                self.room_dirty = True
                print(f"  moved corner #{self.drag_idx + 1} → {(gx, gy)}")
                self.drag_idx = None
                return
            near = nearest_index(self.area_pts, gx, gy)
            if near is not None:
                self.drag_idx = near
                print(f"  picked corner #{near + 1} — click to place")
                return
            if self.insert_mode and len(self.area_pts) >= 2:
                idx, ip = nearest_edge_insert(self.area_pts, gx, gy)
                self.area_pts.insert(idx, (gx, gy))
                self.undo_stack.append(("area_add", idx))
                self.room_dirty = True
                print(f"  inserted corner #{idx + 1} at {(gx, gy)}")
                return
            self.area_pts.append((gx, gy))
            self.undo_stack.append(("area_add", len(self.area_pts) - 1))
            self.room_dirty = True
            print(f"  added corner #{len(self.area_pts)} at {(gx, gy)}")
        elif event == cv2.EVENT_RBUTTONDOWN:
            if self.drag_idx is not None:
                self.drag_idx = None
                print("  cancel move")
                return
            near = nearest_index(self.area_pts, gx, gy, max_dist=22)
            if near is None:
                print("  no corner nearby to delete")
                return
            removed = self.area_pts.pop(near)
            self.undo_stack.append(("area_del", near, removed))
            self.room_dirty = True
            print(f"  deleted corner #{near + 1} {removed}  "
                  f"({len(self.area_pts)} left)")

    def _on_zombie_mouse(self, event, gx: int, gy: int) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.color_pts.append((gx, gy))
            self.undo_stack.append(("inc",))
            self.zombie_dirty = True
            print(f"  INCLUDE sample #{len(self.color_pts)} at {(gx, gy)}")
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.exclude_pts.append((gx, gy))
            self.undo_stack.append(("exc",))
            self.zombie_dirty = True
            print(f"  EXCLUDE sample #{len(self.exclude_pts)} at {(gx, gy)} "
                  f"(bones/floor)")

    def undo(self) -> None:
        if self.drag_idx is not None:
            self.drag_idx = None
            print("  cancel move")
            return
        if not self.undo_stack:
            # Fallback: pop last of current mode.
            if self.mode == "area" and self.area_pts:
                self.area_pts.pop()
                print("  undid last corner")
            elif self.mode == "zombie" and self.color_pts:
                self.color_pts.pop()
                print("  undid last include")
            return
        kind = self.undo_stack.pop()
        if kind[0] == "area_add":
            idx = kind[1]
            if 0 <= idx < len(self.area_pts):
                self.area_pts.pop(idx)
            print("  undid corner add")
        elif kind[0] == "area_del":
            _, idx, pt = kind
            self.area_pts.insert(idx, pt)
            print(f"  restored corner at {pt}")
        elif kind[0] == "inc" and self.color_pts:
            self.color_pts.pop()
            print("  undid include sample")
        elif kind[0] == "exc" and self.exclude_pts:
            self.exclude_pts.pop()
            print("  undid exclude sample")
        elif kind[0] == "probe_set":
            _, cfg_key, prev = kind
            if prev is None:
                self.probe_entries.pop(cfg_key, None)
            else:
                self.probe_entries[cfg_key] = prev
            print(f"  undid {cfg_key} probe set")
        elif kind[0] == "probe_clear":
            _, cfg_key, old = kind
            self.probe_entries[cfg_key] = old
            print(f"  restored {cfg_key} probe")


def main() -> int:
    win = window.find_game()
    if win is None:
        print("RuneScape is not open. Start the game, then run this again.")
        return 1

    cfg = load_cfg()
    grab = capture.Grabber()

    cv2.namedWindow("RS3 mark", cv2.WINDOW_AUTOSIZE)

    try:
        win, freeze, _mhit = freeze_game(grab)
    except RuntimeError as exc:
        print(exc)
        return 1

    marker = Marker()
    marker.freeze = freeze
    marker.win_w = win.width
    marker.win_h = win.height
    print("Mark your area, zombie colours, and UI probes.")

    fractions = aoi.normalize_polygon((cfg.get("aoi") or {}).get("polygon") or [])
    if fractions:
        marker.area_pts = [
            (int(round(fx * win.width)), int(round(fy * win.height)))
            for fx, fy in fractions
        ]

    # Seed marker search box if previously saved.
    raw_box = (cfg.get("aoi") or {}).get("search_rect")
    if raw_box and len(raw_box) == 4:
        x1, y1, x2, y2 = (float(v) for v in raw_box)
        if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
            marker.search_corners = [
                (int(round(x1 * win.width)), int(round(y1 * win.height))),
                (int(round(x2 * win.width)), int(round(y2 * win.height))),
            ]
        else:
            marker.search_corners = [
                (int(round(x1)), int(round(y1))),
                (int(round(x2)), int(round(y2))),
            ]
        print(f"Loaded search box {marker.search_rect_px()}")

    # Seed UI probes from last save (persist across runs).
    saved_probes = _load_saved_probes(cfg)
    marker.probe_entries = dict(saved_probes)
    pcfg = (cfg.get("probes") if isinstance(cfg.get("probes"), dict) else {}) or {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            raw_p = json.load(f).get("probes") or {}
        if isinstance(raw_p, dict) and "tolerance" in raw_p:
            pcfg = raw_p
    probe_tol = int(pcfg.get("tolerance", probes.DEFAULT_TOLERANCE))
    if saved_probes:
        print(f"Loaded {len(saved_probes)}/3 UI probes from last save "
              f"(h/d/t kept — only re-click to change).")
        for name, entry in saved_probes.items():
            print(f"  {name}: frac={entry['xy_frac']}  BGR={entry['bgr']}")
    else:
        print("No UI probes saved yet — set them once with h / d / t, then s.")

    tcfg = cfg.setdefault("targeting", {})
    colors: list[list[int]] = list(tcfg.get("zombie_colors_bgr") or [])
    excludes: list[list[int]] = list(tcfg.get("zombie_exclude_bgr") or [])
    tolerance = int(tcfg.get("color_tolerance", DEFAULT_COLOR_TOL))
    if tolerance > MAX_COLOR_TOL:
        tolerance = DEFAULT_COLOR_TOL
    exclude_tol = int(tcfg.get("exclude_tolerance",
                               targets._DEFAULT_EXCLUDE_TOLERANCE))
    deadzone_frac = float((cfg.get("aoi") or {}).get(
        "deadzone_frac", aoi.DEFAULT_DEADZONE_FRAC))

    cv2.setMouseCallback("RS3 mark", marker.on_mouse)

    print("=" * 64)
    print("ROOM LOCK (when needed):")
    print("  1) Cyan V on room floor  2) v=search box  3) a=fence  4) z=colours")
    print("UI PROBES (optional to re-do — kept from last save):")
    print("  h / d / t only if you want to CHANGE them; otherwise just s")
    print("=" * 64)
    print("Keys: a/z/v  h/d/t probes  r refresh  s SAVE  q quit")
    print("Note: x clears fence/colours only — probes are NOT cleared.")

    while True:
        view = freeze.copy()
        ax, ay = win.width // 2, win.height // 2
        dz = aoi.deadzone_radius(win, deadzone_frac)
        cv2.drawMarker(view, (ax, ay), (60, 60, 255), cv2.MARKER_CROSS, 22, 2)
        cv2.circle(view, (ax, ay), dz, (60, 60, 255), 2)

        live_inc = list(colors)
        for px, py in marker.color_pts:
            c = sample_pixel_bgr(freeze, px, py)
            if c:
                live_inc.append(c)
        live_exc = list(excludes)
        for px, py in marker.exclude_pts:
            c = sample_pixel_bgr(freeze, px, py)
            if c:
                live_exc.append(c)

        if live_inc:
            mask = targets.mask_from_colors(view, live_inc, tolerance)
            if live_exc:
                mask &= ~targets.mask_from_colors(view, live_exc, exclude_tol)
            if len(marker.area_pts) >= 3:
                poly = np.array(marker.area_pts, dtype=np.int32)
                inside = np.zeros(mask.shape, dtype=np.uint8)
                cv2.fillPoly(inside, [poly], 255)
                mask = mask & (inside > 0)
            tint = view.copy()
            tint[mask] = (255, 0, 255)
            view = cv2.addWeighted(view, 0.65, tint, 0.35, 0)

        if len(marker.area_pts) >= 2:
            pts = np.array(marker.area_pts, dtype=np.int32)
            closed = len(marker.area_pts) >= 3
            cv2.polylines(view, [pts], closed, (80, 220, 80), 2)
            if closed:
                overlay = view.copy()
                cv2.fillPoly(overlay, [pts], (40, 120, 40))
                view = cv2.addWeighted(view, 0.85, overlay, 0.15, 0)
        for i, p in enumerate(marker.area_pts):
            col = (0, 255, 255) if i != marker.drag_idx else (0, 128, 255)
            cv2.circle(view, p, 6, col, -1)
            cv2.putText(view, str(i + 1), (p[0] + 6, p[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
        for p in marker.color_pts:
            cv2.circle(view, p, 6, (255, 0, 255), 2)
            cv2.circle(view, p, 2, (255, 0, 255), -1)
        for p in marker.exclude_pts:
            cv2.drawMarker(view, p, (0, 165, 255), cv2.MARKER_TILTED_CROSS, 12, 2)

        box = marker.search_rect_px()
        if box is not None:
            cv2.rectangle(view, (box[0], box[1]), (box[2], box[3]),
                          (255, 180, 0), 2)
            cv2.putText(view, "SEARCH", (box[0] + 4, box[1] + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 180, 0), 1)
        elif len(marker.search_corners) == 1:
            cv2.circle(view, marker.search_corners[0], 6, (255, 180, 0), 2)

        for mode_key, (cfg_key, pcol, plabel) in PROBE_MODES.items():
            probes.draw_probe(view, marker.probe_entries.get(cfg_key),
                              plabel, pcol)

        mcfg_preview = aoi._marker_cfg(cfg.get("aoi") or {})
        live_box = marker.search_rect_frac(win.width, win.height)
        if live_box is not None:
            mcfg_preview["search_rect"] = live_box
        tmpl_prev = None
        if aoi.MARKER_TEMPLATE_PATH.exists():
            tmpl_prev = cv2.imread(str(aoi.MARKER_TEMPLATE_PATH), cv2.IMREAD_COLOR)
        mhit_preview = aoi.detect_marker(freeze, mcfg_preview, template=tmpl_prev)
        if mhit_preview is not None:
            cv2.drawMarker(view, (mhit_preview[0], mhit_preview[1]), (255, 220, 0),
                           cv2.MARKER_TILTED_CROSS, 22, 2)

        scale = min(1.0, MAX_PREVIEW_W / view.shape[1])
        marker.scale = scale
        preview = (cv2.resize(view, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_AREA)
                   if scale < 1.0 else view)

        if marker.mode == "area":
            ins = "INSERT-ON" if marker.insert_mode else "append"
            mode_txt = f"AREA: L=add/move  R=delete  i={ins}"
            mode_col = (80, 220, 80)
        elif marker.mode == "search":
            mode_txt = "SEARCH: click 2 corners (keep minimap OUT)  R=clear"
            mode_col = (255, 180, 0)
        elif marker.mode in PROBE_MODES:
            _ck, _pc, plabel = PROBE_MODES[marker.mode]
            tips = {
                "health": "click red fill at eat threshold (change→eat)",
                "adren": "click empty adren bar (change→fighting)",
                "tbar": "click target bar while engaged (match→attack)",
            }
            mode_txt = f"{plabel} PROBE: L=set  R=clear  — {tips[marker.mode]}"
            mode_col = PROBE_MODES[marker.mode][1]
        else:
            mode_txt = "ZOMBIE: L=include body  R=exclude bones/floor"
            mode_col = (255, 0, 255)
        banner = np.zeros((BANNER_H, preview.shape[1], 3), np.uint8)
        mtxt = "V-marker=ON" if mhit_preview else "V-marker=OFF (r when cyan V visible)"
        sbox = marker.search_rect_px()
        stxt = (f"box=({sbox[0]},{sbox[1]})-({sbox[2]},{sbox[3]})"
                if sbox else "box=MISSING — press v !!")
        # Checklist: room lock OR already saved; probes optional but recommended.
        has_box = sbox is not None
        has_fence = len(marker.area_pts) >= 3
        has_zom = len(marker.color_pts) >= 1 or bool(colors)
        has_v = mhit_preview is not None
        has_hp = "health" in marker.probe_entries
        has_ad = "adrenaline" in marker.probe_entries
        has_tb = "target_bar" in marker.probe_entries
        prior_aoi = len((cfg.get("aoi") or {}).get("polygon_offset") or []) >= 3
        room_ok = (has_box and has_fence and has_zom and has_v) or prior_aoi
        chk = (
            f"ROOM: [{'x' if room_ok else ' '}] locked   "
            f"PROBES: [{'x' if has_hp else ' '}]h  "
            f"[{'x' if has_ad else ' '}]d  "
            f"[{'x' if has_tb else ' '}]t   then s"
        )
        lines = [
            "ROOM: v/a/z (+cyan V) when needed | PROBES: h=HP d=adren t=target | s=save",
            f"MODE [{marker.mode.upper()}]  {mode_txt}",
            (f"corners={len(marker.area_pts)}  inc={len(marker.color_pts)}  "
             f"exc={len(marker.exclude_pts)}  tol={tolerance}  "
             f"probe_tol={probe_tol}  {stxt}  {mtxt}"),
            chk,
            "keys: a/z/v  h/d/t  i insert  u undo  c clear  x clear-room  [ ] dz  +/- tol  ,/. probe-tol  r  s  q",
        ]
        for i, line in enumerate(lines):
            if i == 0:
                col = (0, 220, 255)
            elif i == 1:
                col = mode_col
            elif i == 3:
                ready = room_ok and has_hp and has_ad and has_tb
                col = (80, 220, 80) if ready else (80, 80, 255)
            else:
                col = (210, 210, 210)
            cv2.putText(banner, line, (8, 22 + i * 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.40, col, 1)

        cv2.imshow("RS3 mark", np.vstack([banner, preview]))
        key = cv2.waitKey(30) & 0xFF

        if key in (ord("q"), 27):
            cv2.destroyAllWindows()
            print("Quit without saving.")
            return 1
        if key == ord("a"):
            marker.mode = "area"
            marker.drag_idx = None
            print("AREA mode — edit fence corners.")
        elif key == ord("z"):
            marker.mode = "zombie"
            marker.drag_idx = None
            print("ZOMBIE mode — L include / R exclude.")
        elif key == ord("v"):
            marker.mode = "search"
            marker.drag_idx = None
            print("SEARCH BOX — click two opposite corners of the playfield.")
            print("  Tip: keep minimap / action bar OUTSIDE the box.")
        elif key == ord("h"):
            marker.mode = "health"
            marker.drag_idx = None
            print("HEALTH probe — click red bar at your eat threshold.")
            print("  Tip: click ~halfway across the red fill if you eat at 50%.")
        elif key == ord("d"):
            marker.mode = "adren"
            marker.drag_idx = None
            print("ADRENALINE probe — click the EMPTY bar (0%).")
        elif key == ord("t"):
            marker.mode = "tbar"
            marker.drag_idx = None
            print("TARGET-BAR probe — engage a mob, then click the top bar pixel.")
            print("  Tip: press r to refresh freeze while the bar is visible.")
        elif key == ord("i"):
            marker.insert_mode = not marker.insert_mode
            print(f"Insert-on-edge: {'ON' if marker.insert_mode else 'OFF'}")
        elif key == ord("u"):
            marker.undo()
        elif key == ord("c"):
            if marker.mode == "area":
                marker.area_pts = []
                marker.drag_idx = None
                marker.room_dirty = True
                print("Cleared area corners.")
            elif marker.mode == "search":
                marker.search_corners = []
                marker.room_dirty = True
                print("Cleared search box.")
            elif marker.mode in PROBE_MODES:
                cfg_key, _c, label = PROBE_MODES[marker.mode]
                if cfg_key in marker.probe_entries:
                    old = marker.probe_entries.pop(cfg_key)
                    marker.undo_stack.append(("probe_clear", cfg_key, old))
                print(f"Cleared {label} probe.")
            else:
                marker.color_pts = []
                marker.exclude_pts = []
                colors = []
                excludes = []
                marker.zombie_dirty = True
                print("Cleared zombie include + exclude samples.")
        elif key == ord("x"):
            marker.area_pts = []
            marker.color_pts = []
            marker.exclude_pts = []
            marker.search_corners = []
            marker.drag_idx = None
            marker.room_dirty = True
            marker.zombie_dirty = True
            colors = []
            excludes = []
            # Keep health / adren / target-bar probes from last save.
            print("Cleared fence, search box, and zombie colours.")
            print("  (UI probes h/d/t kept — press c in h/d/t mode to clear one)")
        elif key == ord("r"):
            while (cv2.waitKey(1) & 0xFF) not in (255, 0, -1):
                pass
            try:
                win, freeze, _mhit = freeze_game(grab)
            except RuntimeError as exc:
                print(exc)
                continue
            marker.freeze = freeze
            marker.win_w = win.width
            marker.win_h = win.height
            cv2.setMouseCallback("RS3 mark", marker.on_mouse)
            print("Ready — fence/probes kept; image refreshed.")
        elif key == ord("[") or key == ord("{"):
            deadzone_frac = max(0.02, deadzone_frac - 0.005)
        elif key == ord("]") or key == ord("}"):
            deadzone_frac = min(0.20, deadzone_frac + 0.005)
        elif key in (ord("+"), ord("=")):
            tolerance = min(MAX_COLOR_TOL, tolerance + 1)
            print(f"  color tolerance → {tolerance}")
        elif key == ord("-"):
            tolerance = max(3, tolerance - 1)
            print(f"  color tolerance → {tolerance}")
        elif key == ord(","):
            probe_tol = max(5, probe_tol - 2)
            print(f"  probe tolerance → {probe_tol}")
        elif key == ord("."):
            probe_tol = min(80, probe_tol + 2)
            print(f"  probe tolerance → {probe_tol}")
        elif key == ord("9"):
            exclude_tol = max(3, exclude_tol - 1)
            print(f"  exclude tolerance → {exclude_tol}")
        elif key == ord("0"):
            exclude_tol = min(24, exclude_tol + 1)
            print(f"  exclude tolerance → {exclude_tol}")
        elif key == ord("s"):
            prior_aoi = (cfg.get("aoi") or {})
            prior_offset = prior_aoi.get("polygon_offset") or []
            prior_colors = (cfg.get("targeting") or {}).get("zombie_colors_bgr") or []
            updating_fence = marker.room_dirty

            if updating_fence and len(marker.area_pts) < 3:
                print("Need at least 3 area corners before saving a new fence.")
                continue
            if not updating_fence and len(prior_offset) < 3:
                print("No saved fence yet — press a and mark 3+ corners first.")
                continue

            new_colors: list[list[int]] = []
            for px, py in marker.color_pts:
                c = sample_pixel_bgr(freeze, px, py)
                if c:
                    new_colors.append(c)
            uniq = dedupe_colors(new_colors, min_delta=6)
            if marker.zombie_dirty:
                if not uniq:
                    print("No zombie colours yet. Press z and LEFT-click a zombie.")
                    continue
            elif not uniq:
                uniq = list(prior_colors)
            if not uniq:
                print("No zombie colours yet. Press z and LEFT-click a zombie pixel.")
                continue
            new_exc: list[list[int]] = []
            for px, py in marker.exclude_pts:
                c = sample_pixel_bgr(freeze, px, py)
                if c:
                    new_exc.append(c)
            uniq_exc = dedupe_colors(new_exc, min_delta=6)
            if not marker.zombie_dirty:
                uniq_exc = list(
                    (cfg.get("targeting") or {}).get("zombie_exclude_bgr") or [])
                if new_exc:
                    uniq_exc = dedupe_colors(new_exc, min_delta=6)

            # Resolve probes: session click wins, else last save. Never force
            # re-click when only the fence / zombie colours changed.
            prior_probes = _load_saved_probes(cfg)
            resolved: dict[str, dict] = {}
            for name in ("health", "adrenaline", "target_bar"):
                if name in marker.probe_entries:
                    resolved[name] = marker.probe_entries[name]
                elif name in prior_probes:
                    resolved[name] = prior_probes[name]
            missing = [n for n in ("health", "adrenaline", "target_bar")
                       if n not in resolved]
            if missing:
                labels = {"health": "h", "adrenaline": "d", "target_bar": "t"}
                need = ", ".join(f"{labels[n]}={n}" for n in missing)
                print(f"Missing probes (first-time only): {need}")
                print("  Set them once, then future AOI saves keep them automatically.")
                continue

            # Room lock: re-resolve cyan marker when fence is being rewritten.
            mx = my = None
            mscore = 0.0
            if updating_fence:
                mcfg = aoi._marker_cfg({})
                tmpl = None
                if aoi.MARKER_TEMPLATE_PATH.exists():
                    tmpl = cv2.imread(str(aoi.MARKER_TEMPLATE_PATH),
                                     cv2.IMREAD_COLOR)
                mhit = aoi.detect_marker(freeze, mcfg, template=tmpl)
                if mhit is None:
                    print("Marker OFF on freeze — catching live blink (up to 2s)…")
                    mhit = aoi.wait_for_marker(
                        grab.grab, win.region, mcfg,
                        timeout_s=MARKER_CATCH_TIMEOUT_S, template=tmpl)
                if mhit is None:
                    print("No cyan marker found. Press r, then s again.")
                    continue
                mx, my, mscore = mhit
                if aoi._in_ui_zone(mx, my, win.width, win.height):
                    print("Marker landed on UI/minimap — that glues the fence to YOU.")
                    print("Keep the cyan V on the open floor, press r, then s.")
                    continue
                print(f"  marker at ({mx},{my}) score={mscore:.2f}")

                search_frac = marker.search_rect_frac(win.width, win.height)
                if search_frac is None:
                    search_frac = prior_aoi.get("search_rect")
                if search_frac is None:
                    print("BLOCKED: search box is mandatory. Press v, click 2 corners.")
                    continue
                if not aoi.MARKER_TEMPLATE_PATH.exists():
                    print("BLOCKED: missing reference/markers/cyan_marker_crop.png")
                    continue

                cfg.setdefault("aoi", {})
                cfg["aoi"]["polygon"] = [
                    [round(x / win.width, 4), round(y / win.height, 4)]
                    for x, y in marker.area_pts
                ]
                cfg["aoi"]["polygon_offset"] = aoi.offsets_from_marker(
                    marker.area_pts, mx, my, win.width, win.height)
                cfg["aoi"]["lock_mode"] = "cyan_marker"
                cfg["aoi"]["search_rect"] = search_frac
                print(f"  search_rect fractions: {search_frac}")
                cfg["aoi"]["marker"] = {
                    "hsv_low": mcfg["hsv_low"],
                    "hsv_high": mcfg["hsv_high"],
                    "min_area": mcfg["min_area"],
                    "max_area": mcfg["max_area"],
                    "blink_period_s": 0.5,
                    "holdover_s": 0.60,
                    "arm_window_s": 2.5,
                    "lost_after_s": 1.2,
                    "template_path": str(aoi.MARKER_TEMPLATE_PATH),
                    "template_threshold": 0.62,
                }
                for dead in ("patches", "scene", "scene_threshold",
                             "match_threshold", "min_patch_hits", "inlier_px",
                             "min_orb_inliers"):
                    cfg["aoi"].pop(dead, None)
                cfg["aoi"]["_note"] = (
                    "Fence locked to cyan ground marker. "
                    "Re-mark after zoom changes: python3 mark.py"
                )
                cfg["aoi"]["deadzone_frac"] = round(deadzone_frac, 4)
            else:
                print("  keeping previously saved fence / search box")
                cfg.setdefault("aoi", {})
                cfg["aoi"]["deadzone_frac"] = round(deadzone_frac, 4)

            cfg.setdefault("targeting", {})
            cfg["targeting"]["enabled"] = True
            cfg["targeting"]["click_nearest"] = False
            cfg["targeting"].setdefault("min_blob_area", 180)
            cfg["targeting"].setdefault("max_blob_area", 25000)
            cfg["targeting"].setdefault("relative_area_frac", 0.35)
            cfg["targeting"].setdefault("target_bar_arm_s", 3.0)
            cfg["targeting"].setdefault("retarget_cooldown_s", 3.0)
            cfg["targeting"].pop("click_cooldown_s", None)
            cfg["targeting"]["zombie_colors_bgr"] = uniq
            cfg["targeting"]["zombie_exclude_bgr"] = uniq_exc
            cfg["targeting"]["color_tolerance"] = tolerance
            cfg["targeting"]["exclude_tolerance"] = exclude_tol

            cfg["probes"] = {
                "_comment": (
                    "Single-pixel watchers from mark.py (h/d/t). "
                    "health change→eat, adren change→fight, "
                    "target_bar match→under attack."
                ),
                "tolerance": probe_tol,
                "health": resolved["health"],
                "adrenaline": resolved["adrenaline"],
                "target_bar": resolved["target_bar"],
            }
            # Keep session in sync so the banner stays green after save logic.
            marker.probe_entries = dict(resolved)

            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2)
            cv2.destroyAllWindows()
            print(f"\nSaved to {CONFIG_PATH}")
            if updating_fence:
                print(f"  area corners: {len(marker.area_pts)}")
                print(f"  lock: cyan marker @ ({mx},{my})")
            print(f"  zombie include: {len(uniq)}  exclude: {len(uniq_exc)}  "
                  f"tol={tolerance}/{exclude_tol}")
            print(f"  probes (kept/updated): "
                  f"health={resolved['health']['xy_frac']}  "
                  f"adren={resolved['adrenaline']['xy_frac']}  "
                  f"tbar={resolved['target_bar']['xy_frac']}  "
                  f"tol={probe_tol}")
            print(f"  deadzone_frac: {deadzone_frac:.3f}")
            print("\nNext:  python3 run.py --dry-run")
            return 0


if __name__ == "__main__":
    sys.exit(main())
