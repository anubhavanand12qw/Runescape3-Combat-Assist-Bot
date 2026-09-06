"""Refresh health/adrenaline region fractions for the live window size.

Called at bot start so you do not have to recalibrate by hand after a resize.
Preserves AOI, targeting, and eat settings already in config.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from . import bars, capture, locate, window

CONFIG_PATH = Path("config.json")
DEFAULTS_PATH = Path("config.default.json")
ZERO_REF_PATH = Path("reference/adren_zero.npy")


def _load_base() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else DEFAULTS_PATH
    with open(path) as f:
        return json.load(f)


def measure_and_save(*, require_zero_adren: bool = False) -> str | None:
    """Find bars on the live window and write regions into config.json.

    Returns None on success, or an error string. When adrenaline is not at 0%
    the region boxes are still saved, but the 0% photo is left unchanged unless
    require_zero_adren is False and the bar is empty.
    """
    win = window.find_game()
    if win is None:
        return "RuneScape is not open"

    grab = capture.Grabber()
    shot = grab.grab(win.region)
    found = locate.find_bars(shot)
    if found is None:
        return "Could not find status bars automatically"

    boxes = {
        "hp_bar": found.hp_bar,
        "adren_bar": found.adren_bar,
        "adren_text": found.adren_text,
    }
    ax, ay, aw, ah = boxes["adren_bar"]
    adren_img = shot[ay:ay + ah, ax:ax + aw]
    lit = bars.has_any_yellow(adren_img)
    if require_zero_adren and lit:
        return "Adrenaline is not at 0% — wait for it to drain, then retry"

    cfg = _load_base()
    cfg["regions"] = {
        name: [round(x / win.width, 6), round(y / win.height, 6),
               round(w / win.width, 6), round(h / win.height, 6)]
        for name, (x, y, w, h) in boxes.items()
    }

    hx, hy, hw, hh = boxes["hp_bar"]
    hp_img = shot[hy:hy + hh, hx:hx + hw]
    hsv = cv2.cvtColor(hp_img, cv2.COLOR_BGR2HSV)
    lit_px = int(bars._mask_red(hsv).sum(axis=1).max())
    # Only trust inner width when the fill spans the whole track (true 100%).
    # Near-full bars must not shrink the scale or readings peg at 100%.
    if lit_px > 0 and lit_px >= hw:
        cfg["hp_inner_width"] = lit_px
    else:
        cfg["hp_inner_width"] = hw

    if not lit:
        tx, ty, tw, th = boxes["adren_text"]
        ZERO_REF_PATH.parent.mkdir(exist_ok=True)
        zero_ref = cv2.cvtColor(shot[ty:ty + th, tx:tx + tw],
                                cv2.COLOR_BGR2GRAY).astype(np.float32)
        np.save(ZERO_REF_PATH, zero_ref)
        cv2.imwrite(str(ZERO_REF_PATH.with_suffix(".png")),
                    shot[ty:ty + th, tx:tx + tw])
        cfg["adren_zero_ref"] = str(ZERO_REF_PATH)

    cfg["_comment"] = (
        f"Boxes are fractions of the RuneScape window. "
        f"Measured on live {win.width}x{win.height} window."
    )
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

    print(f"Auto-config: {found.confidence} on {win.width}x{win.height}")
    for name, (x, y, w, h) in boxes.items():
        print(f"   {name:11s} {w}x{h} at ({x}, {y})")
    return None
