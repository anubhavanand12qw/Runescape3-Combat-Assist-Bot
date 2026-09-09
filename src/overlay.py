"""Status readout and AOI targeting overlay windows."""

from __future__ import annotations

import cv2
import numpy as np

W, H = 430, 764
BG = (24, 22, 20)

# Clickable toggle hit-boxes (x0, y0, x1, y1) in overlay pixels.
HIT_ATTACK = (8, 236, 420, 266)
HIT_METHOD = (8, 268, 420, 298)
HIT_EAT = (8, 300, 420, 330)
HIT_LOOT = (8, 332, 420, 362)
HIT_LOOT_INV = (8, 364, 420, 394)
HIT_BURY = (8, 396, 420, 426)
HIT_FREE_STILL = (8, 428, 420, 458)
HIT_FOOD_QUIT = (8, 460, 420, 490)  # quit after eat fail ×N
HIT_CYAN_AVOID = (8, 492, 420, 522)  # skip click if cyan near cursor
HIT_CYAN_SZ = (8, 524, 420, 552)     # cyan avoid box size
HIT_CD = (8, 554, 420, 582)
HIT_HARD = (8, 584, 420, 612)
HIT_STILL_S = (8, 614, 420, 642)
HIT_ARM = (8, 644, 420, 672)
HIT_LOOT_GAP = (8, 674, 420, 702)
HIT_BURY_GAP = (8, 704, 420, 732)

_ATTACK_OPTS = ("bot", "human", "off")
_METHOD_OPTS = ("pixel", "static", "free")
_LOOT_OPTS = ("off", "after_combat", "always")
_BURY_OPTS = ("off", "after_combat", "always")


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

    lx = 150
    cv2.rectangle(canvas, (lx, y + 4), (lx + 16, y + 20),
                  (80, 220, 80) if left_on else (90, 90, 90), 2)
    if left_on:
        cv2.rectangle(canvas, (lx + 3, y + 7), (lx + 13, y + 17), (80, 220, 80), -1)
    cv2.putText(canvas, left, (lx + 22, y + 19), f, 0.42,
                (80, 220, 80) if left_on else (160, 160, 160), 1)

    rx = 280
    cv2.rectangle(canvas, (rx, y + 4), (rx + 16, y + 20),
                  (80, 220, 80) if not left_on else (90, 90, 90), 2)
    if not left_on:
        cv2.rectangle(canvas, (rx + 3, y + 7), (rx + 13, y + 17), (80, 220, 80), -1)
    cv2.putText(canvas, right, (rx + 22, y + 19), f, 0.42,
                (80, 220, 80) if not left_on else (160, 160, 160), 1)


def _three_option_row(canvas, y: int, label: str, options: tuple[str, ...],
                      selected: str) -> None:
    """Draw a three-option toggle row (attack / loot / bury)."""
    f = cv2.FONT_HERSHEY_SIMPLEX
    cv2.rectangle(canvas, (8, y), (W - 10, y + 28), (40, 38, 36), -1)
    cv2.putText(canvas, label, (14, y + 19), f, 0.42, (200, 200, 200), 1)
    start = 118
    gap = 100
    for i, opt in enumerate(options):
        on = opt == selected
        ox = start + i * gap
        cv2.rectangle(canvas, (ox, y + 4), (ox + 14, y + 20),
                      (80, 220, 80) if on else (90, 90, 90), 2)
        if on:
            cv2.rectangle(canvas, (ox + 3, y + 7), (ox + 11, y + 17),
                          (80, 220, 80), -1)
        shown = {
            "after_combat": "target",
            "always": "always",
            "off": "off",
            "bot": "bot",
            "human": "human",
            "pixel": "pixel",
            "static": "static",
            "free": "free",
        }.get(opt, opt)
        cv2.putText(canvas, shown, (ox + 18, y + 19), f, 0.38,
                    (80, 220, 80) if on else (160, 160, 160), 1)


# Glyph x positions for steppers — keep draw + hit_test in sync.
_STEPPER_MINUS_X = 210
_STEPPER_VALUE_X = 250
_STEPPER_PLUS_X = 380
# Clickable pads around [-] / [+] (not equal thirds of the whole row).
_STEPPER_MINUS_X0 = 195
_STEPPER_MINUS_X1 = 248
_STEPPER_PLUS_X0 = 360
_STEPPER_PLUS_X1 = 420


def _stepper_row(canvas, y: int, label: str, value_txt: str) -> None:
    """Draw a − / value / + row for numeric settings."""
    f = cv2.FONT_HERSHEY_SIMPLEX
    cv2.rectangle(canvas, (8, y), (W - 10, y + 28), (40, 38, 36), -1)
    cv2.putText(canvas, label, (14, y + 19), f, 0.40, (200, 200, 200), 1)
    # Visible hit pads so minus/plus are obvious click targets.
    cv2.rectangle(canvas, (_STEPPER_MINUS_X0, y + 3), (_STEPPER_MINUS_X1, y + 25),
                  (55, 55, 90), -1)
    cv2.rectangle(canvas, (_STEPPER_PLUS_X0, y + 3), (_STEPPER_PLUS_X1 - 2, y + 25),
                  (55, 55, 90), -1)
    cv2.putText(canvas, "[-]", (_STEPPER_MINUS_X, y + 19), f, 0.42, (180, 180, 255), 1)
    cv2.putText(canvas, value_txt[:14], (_STEPPER_VALUE_X, y + 19), f, 0.40,
                (80, 220, 80), 1)
    cv2.putText(canvas, "[+]", (_STEPPER_PLUS_X, y + 19), f, 0.42, (180, 180, 255), 1)


def _hit_third(hit: tuple[int, int, int, int], x: int,
               options: tuple[str, ...]) -> str:
    """Map click x inside a hit box to one of the options."""
    x0, _y0, x1, _y1 = hit
    start = x0 + 100
    width = max(1, x1 - start)
    rel = max(0, min(width - 1, x - start))
    idx = min(len(options) - 1, rel * len(options) // width)
    return options[idx]


def _stepper_dir(hit: tuple[int, int, int, int], x: int) -> str | None:
    """Return 'dec' or 'inc' for clicks on drawn [-]/[+], not row thirds.

    Equal thirds mapped the label to dec and put [-] in a dead zone, so clicks
    near the value / toward [+] often registered as inc (minus felt inverted).
    """
    del hit  # row y already matched; x zones are global for all steppers
    if _STEPPER_MINUS_X0 <= x <= _STEPPER_MINUS_X1:
        return "dec"
    if _STEPPER_PLUS_X0 <= x <= _STEPPER_PLUS_X1:
        return "inc"
    return None


def hit_test(x: int, y: int) -> str | None:
    """Return which overlay control was clicked, or None."""
    if HIT_ATTACK[0] <= x <= HIT_ATTACK[2] and HIT_ATTACK[1] <= y <= HIT_ATTACK[3]:
        return f"attack_{_hit_third(HIT_ATTACK, x, _ATTACK_OPTS)}"
    if HIT_METHOD[0] <= x <= HIT_METHOD[2] and HIT_METHOD[1] <= y <= HIT_METHOD[3]:
        return f"method_{_hit_third(HIT_METHOD, x, _METHOD_OPTS)}"
    if HIT_EAT[0] <= x <= HIT_EAT[2] and HIT_EAT[1] <= y <= HIT_EAT[3]:
        mid = (HIT_EAT[0] + HIT_EAT[2]) // 2
        return "eat_low_hp" if x < mid + 40 else "eat_in_combat"
    if HIT_LOOT[0] <= x <= HIT_LOOT[2] and HIT_LOOT[1] <= y <= HIT_LOOT[3]:
        return f"loot_{_hit_third(HIT_LOOT, x, _LOOT_OPTS)}"
    if (HIT_LOOT_INV[0] <= x <= HIT_LOOT_INV[2]
            and HIT_LOOT_INV[1] <= y <= HIT_LOOT_INV[3]):
        mid = (HIT_LOOT_INV[0] + HIT_LOOT_INV[2]) // 2
        return "loot_inv_off" if x < mid else "loot_inv_on"
    if HIT_BURY[0] <= x <= HIT_BURY[2] and HIT_BURY[1] <= y <= HIT_BURY[3]:
        return f"bury_{_hit_third(HIT_BURY, x, _BURY_OPTS)}"
    if (HIT_FREE_STILL[0] <= x <= HIT_FREE_STILL[2]
            and HIT_FREE_STILL[1] <= y <= HIT_FREE_STILL[3]):
        mid = (HIT_FREE_STILL[0] + HIT_FREE_STILL[2]) // 2
        return "free_still_off" if x < mid else "free_still_on"
    if (HIT_FOOD_QUIT[0] <= x <= HIT_FOOD_QUIT[2]
            and HIT_FOOD_QUIT[1] <= y <= HIT_FOOD_QUIT[3]):
        mid = (HIT_FOOD_QUIT[0] + HIT_FOOD_QUIT[2]) // 2
        return "food_quit_off" if x < mid else "food_quit_on"
    if (HIT_CYAN_AVOID[0] <= x <= HIT_CYAN_AVOID[2]
            and HIT_CYAN_AVOID[1] <= y <= HIT_CYAN_AVOID[3]):
        mid = (HIT_CYAN_AVOID[0] + HIT_CYAN_AVOID[2]) // 2
        return "cyan_avoid_off" if x < mid else "cyan_avoid_on"
    for name, hit in (
        ("cyan_sz", HIT_CYAN_SZ),
        ("cd", HIT_CD),
        ("hard", HIT_HARD),
        ("still_s", HIT_STILL_S),
        ("arm", HIT_ARM),
        ("loot_gap", HIT_LOOT_GAP),
        ("bury_gap", HIT_BURY_GAP),
    ):
        if hit[0] <= x <= hit[2] and hit[1] <= y <= hit[3]:
            direction = _stepper_dir(hit, x)
            if direction:
                return f"{name}_{direction}"
            return None
    return None


def render(*, hp: float, adren: float, fighting: bool, reason: str,
           score: float, status: str, status_colour, last_action: str,
           eat_threshold: float, dry_run: bool, ability_keys: list[str],
           eat_suspended: bool = False, eat_failures: int = 0,
           target_count: int = 0, click_in_s: float | None = None,
           attack_style: str = "bot", attack_method: str = "pixel",
           eat_mode: str = "low_hp",
           loot_mode: str = "off", loot_use_inv: bool = False,
           bury_mode: str = "off",
           free_require_still: bool = False,
           quit_on_eat_fail: bool = False,
           cyan_avoid_enabled: bool = False,
           cyan_avoid_size_px: float = 100.0,
           retarget_cooldown_s: list | tuple | float = (4.5, 6.0),
           free_max_bar_hold_s: float = 40.0,
           static_still_s: float = 2.0,
           target_bar_arm_s: float = 3.0,
           loot_interval_s: list | tuple = (2.0, 5.0),
           bury_interval_s: list | tuple = (2.0, 5.0),
           probe_mode: bool = False, under_attack: bool = False,
           kills: int = 0, kills_per_hour: float = 0.0,
           ) -> np.ndarray:
    """Status panel for health, combat, free timers, loot, and bury."""
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
        cv2.putText(c, "mode: pixel probes (mark.py h/d/t/l/i)", (12, 190), f, 0.4,
                    (150, 150, 150), 1)
    else:
        cv2.putText(c, f"text-diff score {score:5.2f}", (12, 190), f, 0.4, (150, 150, 150), 1)

    if eat_suspended:
        cv2.putText(c, "SAFE STOP: eat failed — loot/bury/combat paused", (12, 210), f, 0.40,
                    (80, 80, 255), 1)
    else:
        cv2.putText(c, f"eat fails: {eat_failures}", (12, 210), f, 0.42,
                    (190, 190, 190), 1)

    cd = f"{click_in_s:.1f}s" if click_in_s is not None else "when bar clears"
    cv2.putText(c, f"targets: {target_count}   next: {cd}", (12, 226), f, 0.42,
                (190, 190, 190), 1)
    cv2.putText(c, f"kills: {int(kills)}   {kills_per_hour:.0f}/hr", (250, 226), f, 0.42,
                (180, 220, 160), 1)
    cv2.putText(c, last_action[:52], (12, 248), f, 0.36, (150, 200, 255), 1)

    style = (attack_style or "bot").lower()
    if style not in _ATTACK_OPTS:
        style = "bot"
    method = (attack_method or "pixel").lower()
    if method not in _METHOD_OPTS:
        method = "pixel"
    loot = (loot_mode or "off").lower()
    if loot not in _LOOT_OPTS:
        loot = "off"
    bury_m = (bury_mode or "off").lower()
    if bury_m not in _BURY_OPTS:
        bury_m = "off"

    _three_option_row(c, HIT_ATTACK[1], "Attack", _ATTACK_OPTS, style)
    _three_option_row(c, HIT_METHOD[1], "Method", _METHOD_OPTS, method)
    _checkbox_row(c, HIT_EAT[1], "Eat", "low HP", "in combat",
                  left_on=(eat_mode != "in_combat"))
    _three_option_row(c, HIT_LOOT[1], "Loot", _LOOT_OPTS, loot)
    _checkbox_row(c, HIT_LOOT_INV[1], "Inv box", "off", "on",
                  left_on=(not loot_use_inv))
    _three_option_row(c, HIT_BURY[1], "Bury", _BURY_OPTS, bury_m)
    _checkbox_row(c, HIT_FREE_STILL[1], "Free still", "off", "on",
                  left_on=(not free_require_still))
    _checkbox_row(c, HIT_FOOD_QUIT[1], "Food quit", "off", "on",
                  left_on=(not quit_on_eat_fail))
    _checkbox_row(c, HIT_CYAN_AVOID[1], "Cyan avoid", "off", "on",
                  left_on=(not cyan_avoid_enabled))

    def _pair_txt(pair) -> str:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            return f"{float(pair[0]):.1f}-{float(pair[1]):.1f}"
        return f"{float(pair):.1f}"

    sz = int(round(float(cyan_avoid_size_px)))
    _stepper_row(c, HIT_CYAN_SZ[1], "Cyan box", f"{sz}x{sz}")
    _stepper_row(c, HIT_CD[1], "Bar wait", _pair_txt(retarget_cooldown_s))
    _stepper_row(c, HIT_HARD[1], "Hard hold", f"{float(free_max_bar_hold_s):.0f}s")
    _stepper_row(c, HIT_STILL_S[1], "Still time", f"{float(static_still_s):.1f}s")
    _stepper_row(c, HIT_ARM[1], "Bar arm", f"{float(target_bar_arm_s):.1f}s")
    _stepper_row(c, HIT_LOOT_GAP[1], "Loot gap", _pair_txt(loot_interval_s))
    _stepper_row(c, HIT_BURY_GAP[1], "Bury gap", _pair_txt(bury_interval_s))

    cv2.putText(c, "b/m/e/o/i/u/s/f/c  steppers=[-/+]  q=quit",
                (12, H - 8), f, 0.32, (120, 120, 120), 1)
    return c
