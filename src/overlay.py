"""Status readout and AOI targeting overlay windows."""

from __future__ import annotations

import cv2
import numpy as np

W, H = 430, 360
BG = (24, 22, 20)

# Clickable toggle hit-boxes (x0, y0, x1, y1) in overlay pixels.
HIT_ATTACK = (8, 268, 420, 298)
HIT_EAT = (8, 300, 420, 330)


def _bar(canvas, x, y, w, h, frac, colour) -> None:
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (60, 58, 55), -1)
    filled = int(w * max(0.0, min(1.0, frac)))
    if filled > 0:
        cv2.rectangle(canvas, (x, y), (x + filled, y + h), colour, -1)
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (110, 108, 105), 1)


def _checkbox_row(canvas, y: int, label: str, left: str, right: str,
                  left_on: bool) -> None:
    """Draw a two-option toggle row."""
    f = cv2.FONT_HERSHEY_SIMPLEX
    cv2.rectangle(canvas, (8, y), (W - 10, y + 28), (40, 38, 36), -1)
    cv2.putText(canvas, label, (14, y + 19), f, 0.42, (200, 200, 200), 1)

    # Left option
    lx = 150
    cv2.rectangle(canvas, (lx, y + 4), (lx + 16, y + 20),
                  (80, 220, 80) if left_on else (90, 90, 90), 2)
    if left_on:
        cv2.rectangle(canvas, (lx + 3, y + 7), (lx + 13, y + 17), (80, 220, 80), -1)
    cv2.putText(canvas, left, (lx + 22, y + 19), f, 0.42,
                (80, 220, 80) if left_on else (160, 160, 160), 1)

    # Right option
    rx = 280
    cv2.rectangle(canvas, (rx, y + 4), (rx + 16, y + 20),
                  (80, 220, 80) if not left_on else (90, 90, 90), 2)
    if not left_on:
        cv2.rectangle(canvas, (rx + 3, y + 7), (rx + 13, y + 17), (80, 220, 80), -1)
    cv2.putText(canvas, right, (rx + 22, y + 19), f, 0.42,
                (80, 220, 80) if not left_on else (160, 160, 160), 1)


def hit_test(x: int, y: int) -> str | None:
    """Return which overlay control was clicked, or None."""
    if HIT_ATTACK[0] <= x <= HIT_ATTACK[2] and HIT_ATTACK[1] <= y <= HIT_ATTACK[3]:
        # Left half = bot, right half = human
        mid = (HIT_ATTACK[0] + HIT_ATTACK[2]) // 2
        return "attack_bot" if x < mid + 40 else "attack_human"
    if HIT_EAT[0] <= x <= HIT_EAT[2] and HIT_EAT[1] <= y <= HIT_EAT[3]:
        mid = (HIT_EAT[0] + HIT_EAT[2]) // 2
        return "eat_low_hp" if x < mid + 40 else "eat_in_combat"
    return None


def render(*, hp: float, adren: float, fighting: bool, reason: str,
           score: float, status: str, status_colour, last_action: str,
           eat_threshold: float, dry_run: bool, ability_keys: list[str],
           eat_suspended: bool = False, eat_failures: int = 0,
           target_count: int = 0, click_in_s: float | None = None,
           attack_style: str = "bot", eat_mode: str = "low_hp",
           probe_mode: bool = False, under_attack: bool = False,
           ) -> np.ndarray:
    """Small status panel for health, combat, eat fail-safe, and targeting."""
    c = np.full((H, W, 3), BG, np.uint8)
    f = cv2.FONT_HERSHEY_SIMPLEX

    cv2.putText(c, status, (12, 26), f, 0.6, status_colour, 2)
    if dry_run:
        cv2.putText(c, "DRY RUN - no keys/clicks sent", (12, 48), f, 0.45,
                    (0, 200, 255), 1)

    if probe_mode:
        hp_txt = "OK" if hp >= 0.5 else "LOW → eat"
        hp_col = (90, 235, 90) if hp >= 0.5 else (80, 80, 255)
        cv2.putText(c, f"Health   {hp_txt}", (12, 80), f, 0.5, hp_col, 1)
        _bar(c, 150, 68, 260, 14, 1.0 if hp >= 0.5 else 0.15, (60, 60, 230))
        adren_txt = "FIGHT" if adren >= 0.5 else "idle"
        adren_col = (40, 190, 235) if adren >= 0.5 else (130, 130, 130)
        cv2.putText(c, f"Adren    {adren_txt}", (12, 112), f, 0.5, adren_col, 1)
        _bar(c, 150, 100, 260, 14, adren, (40, 190, 235))
    else:
        cv2.putText(c, f"Health   {hp * 100:5.1f}%", (12, 80), f, 0.5, (235, 235, 235), 1)
        _bar(c, 150, 68, 260, 14, hp, (60, 60, 230))
        tx = 150 + int(260 * eat_threshold)
        cv2.line(c, (tx, 64), (tx, 86), (0, 230, 255), 1)
        cv2.putText(c, "eat", (tx - 10, 62), f, 0.32, (0, 230, 255), 1)
        cv2.putText(c, f"Adren    {adren * 100:5.1f}%", (12, 112), f, 0.5, (235, 235, 235), 1)
        _bar(c, 150, 100, 260, 14, adren, (40, 190, 235))

    col = (90, 235, 90) if fighting else (130, 130, 130)
    cv2.putText(c, f"FIGHTING: {'YES' if fighting else 'no'}", (12, 148), f, 0.6, col, 2)
    tcol = (0, 220, 255) if under_attack else (130, 130, 130)
    if probe_mode:
        cv2.putText(c, f"TARGET: {'YES' if under_attack else 'no'}", (220, 148), f, 0.55, tcol, 2)
    cv2.putText(c, reason[:46], (12, 170), f, 0.4, (170, 170, 170), 1)
    if probe_mode:
        cv2.putText(c, "mode: pixel probes (mark.py h/d/t)", (12, 190), f, 0.4,
                    (150, 150, 150), 1)
    else:
        cv2.putText(c, f"text-diff score {score:5.2f}", (12, 190), f, 0.4, (150, 150, 150), 1)

    if eat_suspended:
        cv2.putText(c, "EAT SUSPENDED (waiting HP recover)", (12, 214), f, 0.42,
                    (80, 80, 255), 1)
    else:
        cv2.putText(c, f"eat fails: {eat_failures}", (12, 214), f, 0.42,
                    (190, 190, 190), 1)

    cd = f"{click_in_s:.1f}s" if click_in_s is not None else "when bar clears"
    cv2.putText(c, f"targets: {target_count}   next: {cd}", (12, 236), f, 0.42,
                (190, 190, 190), 1)
    cv2.putText(c, last_action[:52], (12, 258), f, 0.38, (150, 200, 255), 1)

    _checkbox_row(c, HIT_ATTACK[1], "Attack", "bot", "human",
                  left_on=(attack_style != "human"))
    _checkbox_row(c, HIT_EAT[1], "Eat", "low HP", "in combat",
                  left_on=(eat_mode != "in_combat"))
    cv2.putText(c, "click boxes  |  b=attack  e=eat  q=quit", (12, H - 8), f, 0.35,
                (120, 120, 120), 1)
    return c
