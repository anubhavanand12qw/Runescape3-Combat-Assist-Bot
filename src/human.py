"""Human-like input layer for keys and mouse.

When ``behavior.attack_style`` is ``\"human\"``, the bot routes presses/clicks
through this module instead of the fast robotic helpers in ``keys.py``.

Combat clicks are intentionally **tight**: NPCs move, so long Bezier arcs,
aim-wobble, and pre-click breaks cause real misclicks. Curves stay subtle;
the final event always lands on the exact target pixel.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import Quartz

from . import keys


def _cfg(human_cfg: dict | None, key: str, default):
    if isinstance(human_cfg, dict) and key in human_cfg:
        return human_cfg[key]
    return default


def _uniform(pair, fallback: tuple[float, float]) -> float:
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        lo, hi = float(pair[0]), float(pair[1])
        if hi < lo:
            lo, hi = hi, lo
        return random.uniform(lo, hi)
    return random.uniform(*fallback)


def _cursor_pos() -> tuple[float, float]:
    event = Quartz.CGEventCreate(None)
    point = Quartz.CGEventGetLocation(event)
    return float(point.x), float(point.y)


def _move_event(x: float, y: float, mode: str, pid: int | None) -> None:
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    move = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventMouseMoved, (x, y), Quartz.kCGMouseButtonLeft)
    keys._post(move, mode, pid)


def _bezier_point(t: float, p0, p1, p2, p3) -> tuple[float, float]:
    u = 1.0 - t
    x = (u ** 3) * p0[0] + 3 * (u ** 2) * t * p1[0] + 3 * u * (t ** 2) * p2[0] + (t ** 3) * p3[0]
    y = (u ** 3) * p0[1] + 3 * (u ** 2) * t * p1[1] + 3 * u * (t ** 2) * p2[1] + (t ** 3) * p3[1]
    return x, y


def move_mouse_human(x: int, y: int, human_cfg: dict | None = None,
                     mode: str = "hid", pid: int | None = None,
                     *, style: str = "click") -> None:
    """Move cursor to (x, y) along a visible Bezier path.

    ``style``:
      * ``click`` — curved but quick; ends exactly on target (combat).
      * ``wander`` — slower, more obvious arcs for idle looking-around.
    """
    hcfg = human_cfg or {}
    x0, y0 = _cursor_pos()
    dx, dy = x - x0, y - y0
    dist = math.hypot(dx, dy)
    if dist < 3:
        _move_event(float(x), float(y), mode, pid)
        return

    ox, oy = -dy / dist, dx / dist
    if style == "wander":
        bend_lo, bend_hi, bend_cap = 0.15, 0.35, 90.0
        steps_cfg = _cfg(hcfg, "wander_path_steps", [22, 40])
        sleep_cfg = _cfg(hcfg, "wander_step_sleep_s", [0.008, 0.018])
        jitter = float(_cfg(hcfg, "wander_jitter_px", 1.6))
    else:
        # Visible curve (~0.2–0.45s) then land exactly — still fast enough to hit.
        bend_lo, bend_hi, bend_cap = 0.08, 0.20, 45.0
        steps_cfg = _cfg(hcfg, "click_path_steps", [18, 32])
        sleep_cfg = _cfg(hcfg, "click_step_sleep_s", [0.005, 0.012])
        jitter = float(_cfg(hcfg, "click_jitter_px", 0.5))

    bend = min(bend_cap, random.uniform(bend_lo, bend_hi) * dist)
    bend *= random.choice((-1.0, 1.0))
    p0 = (x0, y0)
    p3 = (float(x), float(y))
    p1 = (x0 + dx * 0.30 + ox * bend, y0 + dy * 0.30 + oy * bend)
    p2 = (x0 + dx * 0.65 + ox * bend * 0.45, y0 + dy * 0.65 + oy * bend * 0.45)

    if isinstance(steps_cfg, (list, tuple)) and len(steps_cfg) == 2:
        n = int(random.randint(int(steps_cfg[0]), int(steps_cfg[1])))
    else:
        n = 28 if style == "wander" else 24
    n = max(10, n)
    if style == "click" and dist < 60:
        n = max(10, int(n * 0.65))

    step_sleep = _uniform(
        sleep_cfg, (0.008, 0.018) if style == "wander" else (0.005, 0.012))

    for i in range(1, n + 1):
        t = i / n
        te = t * t * (3.0 - 2.0 * t)
        px, py = _bezier_point(te, p0, p1, p2, p3)
        if i < n and jitter > 0:
            fade = 1.0 - (i / n) if style == "click" else 0.85
            px += random.uniform(-jitter, jitter) * fade
            py += random.uniform(-jitter, jitter) * fade
        _move_event(px, py, mode, pid)
        time.sleep(step_sleep * random.uniform(0.8, 1.25))

    _move_event(float(x), float(y), mode, pid)


def press_human(key: str, human_cfg: dict | None = None,
                mode: str = "hid", pid: int | None = None) -> None:
    """Key down → hold → key up with human-ish timing."""
    hcfg = human_cfg or {}
    pre = _uniform(_cfg(hcfg, "pre_key_delay_s", [0.04, 0.14]), (0.04, 0.14))
    hold = _uniform(_cfg(hcfg, "key_hold_s", [0.05, 0.12]), (0.05, 0.12))
    post = _uniform(_cfg(hcfg, "post_key_delay_s", [0.04, 0.16]), (0.04, 0.16))
    time.sleep(pre)
    keys.press(key, mode=mode, pid=pid, hold=hold)
    time.sleep(post)


def click_human(x: int, y: int, human_cfg: dict | None = None,
                mode: str = "hid", pid: int | None = None,
                pre_click_ok: Callable[[], bool] | None = None) -> bool:
    """Curved human move, then a click locked to the exact target pixel.

    Args:
        pre_click_ok: Optional zero-arg callable invoked after the cursor is on
            target and just before mouse-down. If it returns False, the click is
            aborted (NPC walked away). Returns True if the click was sent.
    """
    hcfg = human_cfg or {}
    tx, ty = float(x), float(y)
    pre_move = _uniform(_cfg(hcfg, "pre_move_delay_s", [0.03, 0.10]), (0.03, 0.10))
    time.sleep(pre_move)
    move_mouse_human(x, y, hcfg, mode=mode, pid=pid, style="click")

    aim = _uniform(_cfg(hcfg, "aim_settle_s", [0.02, 0.06]), (0.02, 0.06))
    time.sleep(aim)

    # Hard snap — visual path is human; button events never miss.
    _move_event(tx, ty, mode, pid)
    time.sleep(random.uniform(0.01, 0.025))

    if pre_click_ok is not None and not pre_click_ok():
        return False

    hold = _uniform(_cfg(hcfg, "click_hold_s", [0.04, 0.08]), (0.04, 0.08))
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventLeftMouseDown, (tx, ty), Quartz.kCGMouseButtonLeft)
    up = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventLeftMouseUp, (tx, ty), Quartz.kCGMouseButtonLeft)
    keys._post(down, mode, pid)
    time.sleep(hold)
    keys._post(up, mode, pid)

    post = _uniform(_cfg(hcfg, "post_click_delay_s", [0.05, 0.12]), (0.05, 0.12))
    time.sleep(post)
    return True


@dataclass
class HumanSession:
    """Tracks short breaks and idle mouse fidgets between actions."""

    cfg: dict = field(default_factory=dict)
    _next_break_at: float = field(default=0.0, init=False)
    _actions_since_break: int = field(default=0, init=False)
    _next_wander_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.schedule_next_break()
        # First wander after a short beat, then every few seconds.
        self._next_wander_at = time.monotonic() + random.uniform(1.5, 3.5)

    def schedule_next_break(self) -> None:
        pair = self.cfg.get("break_every_actions") or [18, 36]
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            n = random.randint(int(pair[0]), int(pair[1]))
        else:
            n = random.randint(18, 36)
        self._actions_since_break = 0
        gap = _uniform(self.cfg.get("break_max_gap_s") or [60, 150], (60, 150))
        self._next_break_at = time.monotonic() + gap
        self._break_after_actions = n

    def maybe_break(self) -> tuple[str, float] | None:
        """Take a short idle break if due.

        Returns ``(status, duration_s)`` when a break ran, else ``None``.
        Call before ability keys — never immediately before a target click.
        """
        self._actions_since_break += 1
        due = (
            self._actions_since_break >= getattr(self, "_break_after_actions", 24)
            or time.monotonic() >= self._next_break_at
        )
        if not due:
            return None
        if random.random() > float(self.cfg.get("break_chance", 0.55)):
            self.schedule_next_break()
            return None

        dur = _uniform(self.cfg.get("break_duration_s") or [0.4, 1.2], (0.4, 1.2))
        if random.random() < float(self.cfg.get("break_fidget_chance", 0.35)):
            cx, cy = _cursor_pos()
            for _ in range(random.randint(1, 3)):
                nx = cx + random.uniform(-12, 12)
                ny = cy + random.uniform(-10, 10)
                _move_event(nx, ny, "hid", None)
                time.sleep(random.uniform(0.04, 0.10))
            time.sleep(max(0.0, dur - 0.25))
        else:
            time.sleep(dur)
        self.schedule_next_break()
        return f"human break {dur:.1f}s", dur

    def wait_wander(self, bounds: tuple[int, int, int, int],
                    mode: str = "hid", pid: int | None = None) -> None:
        """Visible curved mouse drift while waiting (target bar / cooldown).

        Always moves when the interval elapses (not a rare coin-flip). Stays
        inside ``bounds`` (screen-absolute x0,y0,x1,y1). Never clicks.
        """
        now = time.monotonic()
        if now < self._next_wander_at:
            return

        x0, y0, x1, y1 = bounds
        pad = int(self.cfg.get("wait_wander_pad_px", 36))
        x0, y0 = x0 + pad, y0 + pad
        x1, y1 = x1 - pad, y1 - pad
        if x1 - x0 < 40 or y1 - y0 < 40:
            self._next_wander_at = now + 0.4
            return

        cx, cy = _cursor_pos()
        # Prefer a destination far enough away that the curve is obvious.
        min_dist = float(self.cfg.get("wait_wander_min_dist_px", 70))
        tx, ty = int(cx), int(cy)
        for _ in range(12):
            cand_x = int(random.randint(x0, x1))
            cand_y = int(random.randint(y0, y1))
            if math.hypot(cand_x - cx, cand_y - cy) >= min_dist:
                tx, ty = cand_x, cand_y
                break
        else:
            tx = int(random.randint(x0, x1))
            ty = int(random.randint(y0, y1))

        move_mouse_human(tx, ty, self.cfg, mode=mode, pid=pid, style="wander")
        # Idle gaps of a few seconds — not a twitch every tick.
        self._next_wander_at = now + _uniform(
            self.cfg.get("wait_wander_interval_s") or [2.5, 6.0], (2.5, 6.0))

    def maybe_idle_fidget(self, mode: str = "hid", pid: int | None = None) -> None:
        """Rare tiny cursor drift when not acting (call from idle loop)."""
        if random.random() > float(self.cfg.get("idle_fidget_chance", 0.008)):
            return
        cx, cy = _cursor_pos()
        _move_event(cx + random.uniform(-6, 6), cy + random.uniform(-5, 5),
                    mode, pid)
