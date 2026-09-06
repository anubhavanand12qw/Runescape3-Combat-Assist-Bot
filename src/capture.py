"""Fast screen grabbing of the RuneScape client.

On macOS, ``mss`` reads whatever is painted at those screen points on the
*current* Space. When RuneScape sits on another Space (or under another app
with the same bounds), that silently returns the wrong pixels. We therefore
grab the game via ``CGWindowListCreateImage`` (works off-Space), then crop in
window-local points so existing region fractions keep working.
"""

from __future__ import annotations

import time

import cv2
import mss
import numpy as np
import Quartz

from . import window as winmod

# Reuse one full-window CG capture across the several small crops each tick.
_FRAME_CACHE_S = 0.05


def _cg_window_bgr(window_id: int) -> np.ndarray | None:
    """Full client surface as BGR (Retina pixel size), or None on failure."""
    if window_id <= 0:
        return None
    cgimg = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming
        | Quartz.kCGWindowImageShouldBeOpaque,
    )
    if cgimg is None:
        return None
    width = int(Quartz.CGImageGetWidth(cgimg))
    height = int(Quartz.CGImageGetHeight(cgimg))
    bpr = int(Quartz.CGImageGetBytesPerRow(cgimg))
    provider = Quartz.CGImageGetDataProvider(cgimg)
    data = Quartz.CGDataProviderCopyData(provider)
    if data is None or width < 1 or height < 1:
        return None
    row = bpr // 4
    # Little-endian + skip-first alpha → BGRA in memory.
    bgra = np.frombuffer(data, dtype=np.uint8).reshape(height, row, 4)[:, :width, :]
    return np.ascontiguousarray(bgra[:, :, :3])


def _region_inside_window(region: dict[str, int], win: winmod.GameWindow) -> bool:
    """True when ``region`` lies inside the game window (points)."""
    return (
        region["left"] >= win.x
        and region["top"] >= win.y
        and region["left"] + region["width"] <= win.x + win.width + 1
        and region["top"] + region["height"] <= win.y + win.height + 1
    )


class Grabber:
    """Holds grab state and hands back BGR numpy images.

    Prefer CG window captures for RuneScape regions; fall back to mss for
    anything else. One Grabber belongs to one thread.
    """

    def __init__(self) -> None:
        self._sct = mss.mss()
        self._cache_key: tuple | None = None
        self._cache_img: np.ndarray | None = None
        self._cache_t: float = 0.0

    def _logical_game_frame(self, win: winmod.GameWindow) -> np.ndarray | None:
        """Game window as BGR scaled to ``win.width`` x ``win.height`` points."""
        now = time.monotonic()
        key = (win.window_id, win.x, win.y, win.width, win.height)
        if (
            self._cache_img is not None
            and self._cache_key == key
            and now - self._cache_t < _FRAME_CACHE_S
        ):
            return self._cache_img

        raw = _cg_window_bgr(win.window_id)
        if raw is None:
            return None
        if raw.shape[1] != win.width or raw.shape[0] != win.height:
            raw = cv2.resize(raw, (win.width, win.height), interpolation=cv2.INTER_AREA)
        self._cache_key = key
        self._cache_img = raw
        self._cache_t = now
        return raw

    def grab(self, region: dict[str, int]) -> np.ndarray:
        """Grab a region dict {left, top, width, height} as a BGR image."""
        game = winmod.find_game()
        if game is not None and game.window_id and _region_inside_window(region, game):
            frame = self._logical_game_frame(game)
            if frame is not None:
                x = max(0, region["left"] - game.x)
                y = max(0, region["top"] - game.y)
                w = region["width"]
                h = region["height"]
                x2 = min(frame.shape[1], x + w)
                y2 = min(frame.shape[0], y + h)
                return np.ascontiguousarray(frame[y:y2, x:x2])

        raw = self._sct.grab(region)
        return np.asarray(raw)[:, :, :3]

    def close(self) -> None:
        self._sct.close()
        self._cache_img = None
        self._cache_key = None

    def __enter__(self) -> "Grabber":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
