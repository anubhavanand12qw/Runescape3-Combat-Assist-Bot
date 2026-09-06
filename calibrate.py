#!/usr/bin/env python3
"""Setup tool: work out where your health and adrenaline bars are, and save them.

Run this once, and again any time you move the RS3 interface around.

    python3 calibrate.py

It finds the bars by itself and shows you a zoomed-in picture so you can check.
If a box is wrong you can drag a new one.

  h  redraw the HEALTH bar box        s  save and quit
  a  redraw the ADRENALINE bar box    r  run the auto-finder again
  t  redraw the ADRENALINE TEXT box   q  quit without saving
  n  type in your exact health numbers, for a perfectly scaled reading

IMPORTANT: your adrenaline must read 0% while you do this, because we save a
photo of that "0%" text and use it later to spot the moment it stops being zero.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

from src import bars, capture, locate, window

ZOOM = 3
PAD_X, PAD_Y = 160, 70
CONFIG_PATH = "config.json"
DEFAULTS_PATH = "config.default.json"
ZERO_REF_PATH = "reference/adren_zero.npy"

COLOURS = {"hp_bar": (60, 60, 255), "adren_bar": (0, 200, 255),
           "adren_text": (120, 255, 120)}
LABELS = {"hp_bar": "HEALTH bar (h)", "adren_bar": "ADRENALINE bar (a)",
          "adren_text": "ADRENALINE text (t)"}


class Picker:
    """Lets you drag a fresh box on the zoomed preview."""

    def __init__(self) -> None:
        self.target: str | None = None
        self.start: tuple[int, int] | None = None
        self.current: tuple[int, int, int, int] | None = None
        self.done: tuple[int, int, int, int] | None = None

    def on_mouse(self, event, x, y, flags, param) -> None:
        if self.target is None:
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            self.start = (x, y)
            self.current = None
        elif event == cv2.EVENT_MOUSEMOVE and self.start:
            self.current = (*self.start, x, y)
        elif event == cv2.EVENT_LBUTTONUP and self.start:
            self.done = (*self.start, x, y)
            self.start = None


def to_box(drag: tuple[int, int, int, int], ox: int, oy: int) -> tuple[int, int, int, int]:
    """Zoomed-preview drag -> a box in game-window pixels."""
    x0, y0, x1, y1 = drag
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return (ox + x0 // ZOOM, oy + y0 // ZOOM,
            max(1, (x1 - x0) // ZOOM), max(1, (y1 - y0) // ZOOM))


def auto_only() -> int:
    """Find the boxes and save, with no window and no questions.

    Handy for a quick re-setup, but you see nothing, so check the result with
    `python3 run.py --dry-run` afterwards.
    """
    from src import autocal
    err = autocal.measure_and_save(require_zero_adren=True)
    if err:
        print(err)
        return 1
    print("\nNext:  python3 run.py --dry-run")
    return 0


def main() -> int:
    if "--auto" in sys.argv:
        return auto_only()

    win = window.find_game()
    if win is None:
        print("RuneScape is not open. Start the game, then run this again.")
        return 1
    print(f"Found RuneScape: {win.width}x{win.height} at ({win.x}, {win.y})")

    grab = capture.Grabber()
    shot = grab.grab(win.region)

    found = locate.find_bars(shot)
    if found is None:
        print("Could not find the bars automatically - drag all three boxes by hand.")
        boxes = {"hp_bar": (600, 890, 90, 4), "adren_bar": (740, 890, 90, 4),
                 "adren_text": (735, 873, 55, 15)}
    else:
        print(f"Auto-found the bars: {found.confidence}")
        boxes = {"hp_bar": found.hp_bar, "adren_bar": found.adren_bar,
                 "adren_text": found.adren_text}

    hp_inner_width: int | None = None

    # Crop a zoomed workspace around the bars so small boxes are easy to see.
    bx, by = boxes["hp_bar"][0], boxes["adren_text"][1]
    ox = max(0, bx - PAD_X)
    oy = max(0, by - PAD_Y)
    ex = min(shot.shape[1], boxes["adren_bar"][0] + boxes["adren_bar"][2] + PAD_X)
    ey = min(shot.shape[0], boxes["hp_bar"][1] + boxes["hp_bar"][3] + PAD_Y)

    picker = Picker()
    cv2.namedWindow("RS3 setup", cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback("RS3 setup", picker.on_mouse)

    while True:
        shot = grab.grab(win.region)
        view = cv2.resize(shot[oy:ey, ox:ex], None, fx=ZOOM, fy=ZOOM,
                          interpolation=cv2.INTER_NEAREST)

        for name, (x, y, w, h) in boxes.items():
            p1 = ((x - ox) * ZOOM, (y - oy) * ZOOM)
            p2 = p1[0] + w * ZOOM, p1[1] + h * ZOOM
            cv2.rectangle(view, p1, p2, COLOURS[name], 1)
            cv2.putText(view, LABELS[name], (p1[0], p1[1] - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOURS[name], 1)

        hp_img = shot[boxes["hp_bar"][1]:boxes["hp_bar"][1] + boxes["hp_bar"][3],
                      boxes["hp_bar"][0]:boxes["hp_bar"][0] + boxes["hp_bar"][2]]
        ad_img = shot[boxes["adren_bar"][1]:boxes["adren_bar"][1] + boxes["adren_bar"][3],
                      boxes["adren_bar"][0]:boxes["adren_bar"][0] + boxes["adren_bar"][2]]
        hp_pct = bars.health_fraction(hp_img, hp_inner_width) * 100
        lit = bars.has_any_yellow(ad_img)

        banner = [
            f"health reads {hp_pct:5.1f}%   adrenaline empty: {'NO - not 0%!' if lit else 'yes'}",
            "h/a/t redraw a box   n exact health   r auto-find   s SAVE   q quit",
        ]
        if picker.target:
            banner.append(f"drag a new box for: {LABELS[picker.target]}")
        pad = np.zeros((26 * len(banner) + 8, view.shape[1], 3), np.uint8)
        for i, line in enumerate(banner):
            colour = (0, 220, 255) if picker.target else (230, 230, 230)
            if i == 0 and lit:
                colour = (80, 80, 255)
            cv2.putText(pad, line, (8, 20 + i * 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
        view = np.vstack([pad, view])

        if picker.current:
            x0, y0, x1, y1 = picker.current
            cv2.rectangle(view, (x0, y0 + pad.shape[0]), (x1, y1 + pad.shape[0]),
                          (255, 255, 255), 1)
        if picker.done:
            boxes[picker.target] = to_box(picker.done, ox, oy)
            picker.target, picker.done, picker.current = None, None, None

        cv2.imshow("RS3 setup", view)
        key = cv2.waitKey(30) & 0xFF

        if key in (ord("q"), 27):
            cv2.destroyAllWindows()
            print("Quit without saving.")
            return 1
        if key == ord("h"):
            picker.target = "hp_bar"
        elif key == ord("a"):
            picker.target = "adren_bar"
        elif key == ord("t"):
            picker.target = "adren_text"
        elif key == ord("r"):
            again = locate.find_bars(shot)
            if again:
                boxes = {"hp_bar": again.hp_bar, "adren_bar": again.adren_bar,
                         "adren_text": again.adren_text}
                print(f"Auto-found again: {again.confidence}")
        elif key == ord("n"):
            hp_inner_width = ask_exact_health(hp_img)
        elif key == ord("s"):
            if lit:
                print("\nAdrenaline is NOT at 0%. The saved 0% photo would be wrong,")
                print("so combat detection would misfire. Wait for it to drain, then press s.")
                continue
            cv2.destroyAllWindows()
            return save(win, boxes, hp_inner_width, shot)


def ask_exact_health(hp_img: np.ndarray) -> int | None:
    """Turn your real health numbers into an exact pixel scale.

    The bar has rounded end caps that are not part of its scale, so the box you
    see is a pixel or two wider than the range the fill actually uses. Telling it
    one true reading removes that error for good.
    """
    print("\nLook at the game and type your health, like 358/1200 (or blank to skip):")
    try:
        raw = input("  health> ").strip().replace(",", "")
    except EOFError:
        return None
    if not raw or "/" not in raw:
        print("  skipped")
        return None
    try:
        cur, mx = (float(p) for p in raw.split("/", 1))
        if mx <= 0 or cur < 0:
            raise ValueError
    except ValueError:
        print("  could not read that, skipping")
        return None

    hsv = cv2.cvtColor(hp_img, cv2.COLOR_BGR2HSV)
    lit_pixels = int(bars._mask_red(hsv).sum(axis=1).max())
    true_frac = cur / mx
    if lit_pixels == 0 or true_frac <= 0:
        print("  no red found in the box, skipping")
        return None
    inner = int(round(lit_pixels / true_frac))
    print(f"  {lit_pixels} red pixels = {true_frac*100:.1f}%  ->  bar scale is {inner} px wide")
    return inner


def save(win, boxes, hp_inner_width, shot) -> int:
    path = CONFIG_PATH if Path(CONFIG_PATH).exists() else DEFAULTS_PATH
    with open(path) as f:
        cfg = json.load(f)

    cfg["regions"] = {
        name: [round(x / win.width, 6), round(y / win.height, 6),
               round(w / win.width, 6), round(h / win.height, 6)]
        for name, (x, y, w, h) in boxes.items()
    }
    cfg["hp_inner_width"] = hp_inner_width
    cfg["_comment"] = (
        f"Boxes are fractions of the RuneScape window. "
        f"Measured on live {win.width}x{win.height} window."
    )

    tx, ty, tw, th = boxes["adren_text"]
    zero_ref = cv2.cvtColor(shot[ty:ty + th, tx:tx + tw],
                            cv2.COLOR_BGR2GRAY).astype(np.float32)
    np.save(ZERO_REF_PATH, zero_ref)
    cv2.imwrite("reference/adren_zero.png", shot[ty:ty + th, tx:tx + tw])

    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

    print(f"\nSaved {CONFIG_PATH}")
    for name, (x, y, w, h) in boxes.items():
        print(f"   {name:11s} {w}x{h} at ({x}, {y})")
    print(f"   saved the 0% adrenaline photo to {ZERO_REF_PATH}")
    print(f"   health bar scale: {hp_inner_width or 'box width'} px")
    print("\nNext:  python3 run.py --dry-run    (watches only, presses nothing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
