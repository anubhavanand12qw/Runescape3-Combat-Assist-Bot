"""Single-pixel watchers for health, adrenaline, and the target bar.

Set in ``mark.py`` (keys ``h`` / ``d`` / ``t``). Each probe stores a window
fraction and the BGR colour sampled at save time:

* **health** — sample the red fill at your eat threshold. When the live pixel
  *changes* from that colour, press food.
* **adrenaline** — sample the empty bar. When the live pixel *changes*, treat
  as fighting.
* **target_bar** — sample a pixel of the top target-info bar while engaged.
  When that colour *appears* (matches), treat as under attack.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

DEFAULT_TOLERANCE = 28
SAMPLE_RADIUS = 1  # 3x3 median — resists 1px jitter / AA


def color_distance_bgr(a: list[int] | tuple[int, ...] | np.ndarray,
                       b: list[int] | tuple[int, ...] | np.ndarray) -> float:
    """Sum of absolute BGR channel differences."""
    return float(sum(abs(int(a[i]) - int(b[i])) for i in range(3)))


def sample_bgr(img: np.ndarray, x: int, y: int,
               radius: int = SAMPLE_RADIUS) -> list[int] | None:
    """Median BGR in a small neighbourhood around ``(x, y)``."""
    if img is None or img.size == 0:
        return None
    h, w = img.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return None
    x0 = max(0, x - radius)
    x1 = min(w, x + radius + 1)
    y0 = max(0, y - radius)
    y1 = min(h, y + radius + 1)
    patch = img[y0:y1, x0:x1].reshape(-1, 3)
    med = np.median(patch, axis=0)
    return [int(med[0]), int(med[1]), int(med[2])]


def frac_to_xy(xy_frac: list[float] | tuple[float, ...],
               width: int, height: int) -> tuple[int, int]:
    """Window-local pixel from ``[x_frac, y_frac]``."""
    fx, fy = float(xy_frac[0]), float(xy_frac[1])
    x = int(round(fx * width))
    y = int(round(fy * height))
    return max(0, min(width - 1, x)), max(0, min(height - 1, y))


def xy_to_frac(x: int, y: int, width: int, height: int) -> list[float]:
    return [round(x / width, 6), round(y / height, 6)]


def _probe_entry(probes: dict | None, name: str) -> dict | None:
    if not isinstance(probes, dict):
        return None
    entry = probes.get(name)
    if not isinstance(entry, dict):
        return None
    xy = entry.get("xy_frac")
    bgr = entry.get("bgr")
    if not (isinstance(xy, (list, tuple)) and len(xy) >= 2):
        return None
    if not (isinstance(bgr, (list, tuple)) and len(bgr) >= 3):
        return None
    return entry


def has_probe(probes: dict | None, name: str) -> bool:
    return _probe_entry(probes, name) is not None


def probes_ready(probes: dict | None) -> bool:
    """True when health + adrenaline + target_bar probes are all set."""
    return all(has_probe(probes, n)
               for n in ("health", "adrenaline", "target_bar"))


def tolerance(probes: dict | None) -> int:
    if isinstance(probes, dict) and "tolerance" in probes:
        return max(3, int(probes["tolerance"]))
    return DEFAULT_TOLERANCE


def live_at(frame: np.ndarray, entry: dict) -> list[int] | None:
    """Current BGR at a probe's stored fraction on ``frame``."""
    h, w = frame.shape[:2]
    x, y = frac_to_xy(entry["xy_frac"], w, h)
    return sample_bgr(frame, x, y)


def color_changed(frame: np.ndarray, entry: dict, tol: int) -> bool:
    """True when live pixel differs from the saved baseline colour."""
    live = live_at(frame, entry)
    if live is None:
        return False
    return color_distance_bgr(live, entry["bgr"]) > tol


def color_matches(frame: np.ndarray, entry: dict, tol: int) -> bool:
    """True when live pixel matches the saved colour (within tolerance)."""
    live = live_at(frame, entry)
    if live is None:
        return False
    return color_distance_bgr(live, entry["bgr"]) <= tol


def read_signals(frame: np.ndarray,
                 probes_cfg: dict | None
                 ) -> dict[str, Any]:
    """Evaluate all configured probes on one full-window frame.

    Returns keys:
      health_ok (bool|None) — True when health colour still matches baseline
      need_eat (bool) — health colour changed
      fighting (bool) — adrenaline colour changed
      under_attack (bool) — target_bar colour matches
      adren_lit (float) — 1.0 if fighting else 0.0 (overlay-friendly)
      hp (float) — 1.0 if health_ok else 0.0
      reason (str)
    """
    pcfg = probes_cfg if isinstance(probes_cfg, dict) else {}
    tol = tolerance(pcfg)
    out: dict[str, Any] = {
        "health_ok": None,
        "need_eat": False,
        "fighting": False,
        "under_attack": False,
        "hp": 1.0,
        "adren": 0.0,
        "reason": "no probes",
    }
    reasons: list[str] = []

    health = _probe_entry(pcfg, "health")
    if health is not None:
        changed = color_changed(frame, health, tol)
        out["health_ok"] = not changed
        out["need_eat"] = changed
        out["hp"] = 0.0 if changed else 1.0
        if changed:
            reasons.append("health pixel changed")

    adren = _probe_entry(pcfg, "adrenaline")
    if adren is not None:
        fighting = color_changed(frame, adren, tol)
        out["fighting"] = fighting
        out["adren"] = 1.0 if fighting else 0.0
        if fighting:
            reasons.append("adrenaline pixel changed")

    tbar = _probe_entry(pcfg, "target_bar")
    if tbar is not None:
        under = color_matches(frame, tbar, tol)
        out["under_attack"] = under
        if under:
            reasons.append("target-bar colour present")

    out["reason"] = ", ".join(reasons) or "idle"
    return out


def make_entry(x: int, y: int, bgr: list[int],
               width: int, height: int) -> dict[str, Any]:
    """Build a config probe dict from a click + sampled colour."""
    return {
        "xy_frac": xy_to_frac(x, y, width, height),
        "bgr": [int(bgr[0]), int(bgr[1]), int(bgr[2])],
    }


def draw_probe(view: np.ndarray, entry: dict | None, label: str,
               colour: tuple[int, int, int]) -> None:
    """Draw a labelled crosshair for a saved / live probe."""
    if entry is None:
        return
    h, w = view.shape[:2]
    xy = entry.get("xy_frac")
    if not (isinstance(xy, (list, tuple)) and len(xy) >= 2):
        return
    x, y = frac_to_xy(xy, w, h)
    cv2.drawMarker(view, (x, y), colour, cv2.MARKER_CROSS, 18, 2)
    cv2.circle(view, (x, y), 5, colour, 1)
    cv2.putText(view, label, (x + 10, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1)
