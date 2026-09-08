"""Decide when to press Space to collect loot.

Modes (``behavior.loot_mode``):

* ``off`` — never loot
* ``after_combat`` — Space once each time the target bar clears (no probes).
* ``always`` — keep pressing Space on a random interval (default 2–5s)
  with human timing. If ``behavior.loot_use_inv`` is on, also require loot
  button / blank-inv probes (mark.py ``l`` / ``i``); after-combat still
  fires as a backup.

Key sending stays in the bot; this module only returns a decision.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from . import probes

DEFAULT_ALWAYS_INTERVAL_S = (2.0, 5.0)


@dataclass(frozen=True)
class LootDecision:
    """Result of one loot evaluation tick."""

    should_loot: bool
    reason: str
    needs_post_combat_delay: bool


def _loot_cfg(cfg: dict | None) -> dict:
    return cfg if isinstance(cfg, dict) else {}


def always_interval_range(loot_cfg: dict | None) -> tuple[float, float]:
    """Return (lo, hi) seconds between always-mode Space presses."""
    lcfg = _loot_cfg(loot_cfg)
    pair = lcfg.get("always_interval_s", list(DEFAULT_ALWAYS_INTERVAL_S))
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        lo = float(pair[0])
        hi = float(pair[1])
        if hi < lo:
            lo, hi = hi, lo
        return max(0.0, lo), max(0.0, hi)
    return DEFAULT_ALWAYS_INTERVAL_S


def roll_always_interval(loot_cfg: dict | None) -> float:
    """Pick the next always-mode wait (uniform in configured range)."""
    lo, hi = always_interval_range(loot_cfg)
    if hi <= lo:
        return lo
    return random.uniform(lo, hi)


def loot_probes_ready(probes_cfg: dict | None) -> bool:
    """True when both loot button and blank-inventory probes are set."""
    return probes.has_probe(probes_cfg, "loot_button") and probes.has_probe(
        probes_cfg, "loot_inventory")


def _after_combat_ready(
    loot_cfg: dict | None,
    *,
    now: float,
    bar_cleared_at: float | None,
) -> LootDecision | None:
    """Return a fire decision inside the after-combat window, else None."""
    lcfg = _loot_cfg(loot_cfg)
    window_s = float(lcfg.get("after_combat_window_s", 4.0))
    if bar_cleared_at is None:
        return None
    age = now - bar_cleared_at
    if age < 0 or age > window_s:
        return None
    return LootDecision(True, "loot after combat (bar cleared)", True)


def should_loot(
    frame: Any,
    probes_cfg: dict | None,
    loot_cfg: dict | None,
    loot_mode: str,
    *,
    now: float,
    last_loot_at: float,
    bar_cleared_at: float | None,
    use_inv: bool = False,
    always_gap_s: float = 0.0,
) -> LootDecision:
    """Return whether the bot should press Space this tick.

    Args:
        use_inv: When True and mode is ``always``, gate on loot_button /
            loot_inventory probes. When False, ``always`` presses Space on the
            rolled ``always_gap_s`` interval. ``after_combat`` never needs probes.
        always_gap_s: Seconds required since last loot in ``always`` mode
            (rolled by the bot after each press; 0 allows an immediate first).
    """
    mode = (loot_mode or "off").strip().lower()
    if mode in ("", "off", "disabled", "none"):
        return LootDecision(False, "loot off", False)

    lcfg = _loot_cfg(loot_cfg)
    if mode == "always":
        gap_s = max(0.0, float(always_gap_s))
    else:
        gap_s = float(lcfg.get("cooldown_s", 1.2))
    if (now - last_loot_at) < gap_s:
        return LootDecision(False, "loot cooldown", False)

    if mode in ("after_combat", "after-combat", "combat", "target"):
        hit = _after_combat_ready(
            loot_cfg, now=now, bar_cleared_at=bar_cleared_at)
        if hit is not None:
            return hit
        if bar_cleared_at is None:
            return LootDecision(False, "waiting for combat end", False)
        return LootDecision(False, "outside after-combat window", False)

    if mode == "always":
        if not use_inv:
            # Space-only: random interval between presses, no loot probes.
            return LootDecision(True, "loot always (Space)", False)

        pcfg = probes_cfg if isinstance(probes_cfg, dict) else {}
        tol = probes.tolerance(pcfg)
        inv = probes.get_probe(pcfg, "loot_inventory")
        btn = probes.get_probe(pcfg, "loot_button")
        btn_ok = bool(btn is not None and probes.color_matches(frame, btn, tol))
        inv_ok = bool(inv is not None and probes.color_changed(frame, inv, tol))
        if btn_ok and inv_ok:
            return LootDecision(True, "loot always (inv+button)", False)
        if btn_ok:
            return LootDecision(True, "loot always (button)", False)
        if inv_ok:
            return LootDecision(True, "loot always (inv)", False)
        hit = _after_combat_ready(
            loot_cfg, now=now, bar_cleared_at=bar_cleared_at)
        if hit is not None:
            return LootDecision(True, "loot always (after combat backup)", True)
        if not loot_probes_ready(probes_cfg):
            return LootDecision(False, "loot probes missing (mark.py l/i)", False)
        return LootDecision(
            False,
            f"no loot signal (btn={btn_ok} inv={inv_ok})",
            False,
        )

    return LootDecision(False, f"unknown loot_mode {mode!r}", False)
