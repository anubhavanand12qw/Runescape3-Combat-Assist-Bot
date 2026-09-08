"""Decide when to press the bury-bones key (human timing in the bot).

Modes (``behavior.bury_mode``):

* ``off`` — never bury
* ``after_combat`` — once each time the target bar clears
* ``always`` — keep pressing on a random interval (``bury.always_interval_s``)

Mirrors loot always/after-combat without inventory probes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

DEFAULT_ALWAYS_INTERVAL_S = (2.0, 5.0)


@dataclass(frozen=True)
class BuryDecision:
    """Result of one bury evaluation tick."""

    should_bury: bool
    reason: str
    needs_post_combat_delay: bool


def _bury_cfg(cfg: dict | None) -> dict:
    return cfg if isinstance(cfg, dict) else {}


def always_interval_range(bury_cfg: dict | None) -> tuple[float, float]:
    """Return (lo, hi) seconds between always-mode bury presses."""
    bcfg = _bury_cfg(bury_cfg)
    pair = bcfg.get("always_interval_s", list(DEFAULT_ALWAYS_INTERVAL_S))
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        lo = float(pair[0])
        hi = float(pair[1])
        if hi < lo:
            lo, hi = hi, lo
        return max(0.0, lo), max(0.0, hi)
    return DEFAULT_ALWAYS_INTERVAL_S


def roll_always_interval(bury_cfg: dict | None) -> float:
    """Pick the next always-mode wait (uniform in configured range)."""
    lo, hi = always_interval_range(bury_cfg)
    if hi <= lo:
        return lo
    return random.uniform(lo, hi)


def _after_combat_ready(
    bury_cfg: dict | None,
    *,
    now: float,
    bar_cleared_at: float | None,
) -> BuryDecision | None:
    """Return a fire decision inside the after-combat window, else None."""
    bcfg = _bury_cfg(bury_cfg)
    window_s = float(bcfg.get("after_combat_window_s", 4.0))
    if bar_cleared_at is None:
        return None
    age = now - bar_cleared_at
    if age < 0 or age > window_s:
        return None
    return BuryDecision(True, "bury after combat (bar cleared)", True)


def should_bury(
    bury_cfg: dict | None,
    bury_mode: str,
    *,
    now: float,
    last_bury_at: float,
    bar_cleared_at: float | None,
    always_gap_s: float = 0.0,
) -> BuryDecision:
    """Return whether the bot should press the bury key this tick."""
    mode = (bury_mode or "off").strip().lower()
    if mode in ("", "off", "disabled", "none"):
        return BuryDecision(False, "bury off", False)

    bcfg = _bury_cfg(bury_cfg)
    if mode == "always":
        gap_s = max(0.0, float(always_gap_s))
    else:
        gap_s = float(bcfg.get("cooldown_s", 1.2))
    if (now - last_bury_at) < gap_s:
        return BuryDecision(False, "bury cooldown", False)

    if mode in ("after_combat", "after-combat", "combat", "target"):
        hit = _after_combat_ready(
            bury_cfg, now=now, bar_cleared_at=bar_cleared_at)
        if hit is not None:
            return hit
        if bar_cleared_at is None:
            return BuryDecision(False, "waiting for combat end", False)
        return BuryDecision(False, "outside after-combat window", False)

    if mode == "always":
        return BuryDecision(True, "bury always", False)

    return BuryDecision(False, f"unknown bury_mode {mode!r}", False)
