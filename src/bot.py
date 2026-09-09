"""The brain: one loop that watches probes/bars, eats safely, and clicks zombies.

    is the game in front?  no -> do nothing
    health probe changed?  yes -> press food (stops after 2 failed eats)
    loot mode on?          yes -> after combat / always: Space when probes say so
    bury mode on?          yes -> after combat / always: ] (bury_key) like loot
    targeting enabled?     yes -> detect zombies in AOI; click when target bar clear
    fighting + abilities?  yes -> press the next ability key from the config

Preferred signals come from single-pixel probes set in ``mark.py`` (h/d/t).
Bar-region reading remains a fallback when probes are not configured.
"""

from __future__ import annotations

import itertools
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import (
    aoi, bars, bury, capture, combat, config_util, free_pixel, human, keys, loot,
    overlay, probes, static_target, targets, window,
)


@dataclass
class Bot:
    cfg: dict
    dry_run: bool = False
    paused: bool = False
    stop: bool = False

    _grab: capture.Grabber = field(init=False)
    _combat: combat.CombatDetector = field(init=False)
    _human: human.HumanSession = field(init=False)
    _cycle: itertools.cycle | None = field(default=None, init=False)
    _last_eat: float = field(default=-1e9, init=False)
    _last_ability: float = field(default=-1e9, init=False)
    _miss_xy: tuple[int, int] | None = field(default=None, init=False)
    _clicked_at: float = field(default=-1e9, init=False)
    _bar_was_on: bool = field(default=False, init=False)
    _retarget_after: float = field(default=-1e9, init=False)
    _last_action: str = field(default="nothing yet", init=False)
    _log: object = field(default=None, init=False)
    _overlay_ready: bool = field(default=False, init=False)
    _overlay_dirty: bool = field(default=False, init=False)
    _overlay_cache: dict = field(default_factory=dict, init=False)
    _use_probes: bool = field(default=False, init=False)

    # Eat fail-safe: stop pressing food after N failed attempts until HP recovers.
    # When suspended, also pause loot + combat (out of food / inventory stuck).
    _eat_failures: int = field(default=0, init=False)
    _eat_suspended: bool = field(default=False, init=False)
    _hp_at_eat: float | None = field(default=None, init=False)
    _awaiting_eat_result: bool = field(default=False, init=False)
    _eat_resume_ok_since: float | None = field(default=None, init=False)

    _target_count: int = field(default=0, init=False)
    _last_pick: tuple[int, int] | None = field(default=None, init=False)
    _fence: aoi.MarkerTracker = field(default_factory=aoi.MarkerTracker, init=False)
    _fence_lost: bool = field(default=False, init=False)
    _static: static_target.StaticTargetTracker = field(
        default_factory=static_target.StaticTargetTracker, init=False)
    _free: free_pixel.FreePixelState = field(
        default_factory=free_pixel.FreePixelState, init=False)
    # Free-mode target-bar gate is engagement-scoped (only after OUR click).
    _free_expect_bar: bool = field(default=False, init=False)
    _free_saw_bar: bool = field(default=False, init=False)
    _free_bar_on_since: float = field(default=-1e9, init=False)
    # Rolled once when the target bar appears (supports [lo, hi] hard hold).
    _free_bar_hold_limit_s: float = field(default=40.0, init=False)
    # After a fight bar clears: watch this window for an auto-new target bar
    # before searching/clicking another NPC.
    _free_post_clear_watch: bool = field(default=False, init=False)
    # After max bar-hold: ignore bar and force a new click (bar may still be on).
    _free_force_retarget: bool = field(default=False, init=False)
    # After a force click, wait for bar to go OFF once before holding again.
    _free_need_bar_edge: bool = field(default=False, init=False)

    _last_loot_at: float = field(default=-1e9, init=False)
    _loot_always_gap_s: float = field(default=0.0, init=False)
    _loot_bar_cleared_at: float | None = field(default=None, init=False)
    _loot_bar_was_on: bool = field(default=False, init=False)
    _loot_handled_clear_at: float | None = field(default=None, init=False)
    _warned_loot_probes: bool = field(default=False, init=False)

    _last_bury_at: float = field(default=-1e9, init=False)
    _bury_always_gap_s: float = field(default=0.0, init=False)
    _bury_handled_clear_at: float | None = field(default=None, init=False)
    # Mute Space / bury while resting (human break, post-click cooldown, SAFE STOP).
    _support_pause_until: float = field(default=-1e9, init=False)
    _skip_support_keys_once: bool = field(default=False, init=False)
    # Kill proxy: target-bar ON→OFF edges this session (overlay only).
    _session_started_at: float = field(default=0.0, init=False)
    _kills: int = field(default=0, init=False)
    _session_summary_printed: bool = field(default=False, init=False)
    _click_abort_reason: str = field(default="", init=False)
    # Camera rotate (Right Arrow): non-blocking press/release schedule.
    _rotate_held: bool = field(default=False, init=False)
    _rotate_release_at: float = field(default=-1e9, init=False)
    _next_rotate_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._grab = capture.Grabber()
        aoi_cfg = self.cfg.get("aoi") or {}
        if aoi_cfg.get("polygon_offset") and aoi_cfg.get("lock_mode") == "cyan_marker":
            print("World-lock: blinking cyan ground marker.")
        else:
            print("WARNING: fence is not marker-locked yet.")
            print("         Your config still has the old terrain lock (or none).")
            print("         Fix:  python3 mark.py  → draw fence + zombie colours → s")
            print("         (cyan marker must be visible/blinking when you save)")

        self._use_probes = probes.probes_ready(self.cfg.get("probes"))
        if self._use_probes:
            print("Signals: pixel probes (mark.py h/d/t) — health/adren/target-bar.")
            self._combat = combat.CombatDetector()
        else:
            print("Signals: bar regions (fallback). Set probes in mark.py: h / d / t")
            ref_path = Path(self.cfg.get("adren_zero_ref") or "reference/adren_zero.npy")
            zero_ref = np.load(ref_path) if ref_path.exists() else None
            if zero_ref is None:
                print("WARNING: no saved 0% adrenaline photo. Combat detection will lean on")
                print("         the bar and health drops only. Run mark.py (h/d/t) or calibrate.py.")
            self._combat = combat.CombatDetector(
                zero_ref=zero_ref,
                text_threshold=float(self.cfg.get("text_threshold", 4.0)),
                hp_drop_window=float((self.cfg.get("combat") or {}).get(
                    "hp_drop_window_s", 3.0)),
                hp_drop_min=float((self.cfg.get("combat") or {}).get(
                    "hp_drop_min_frac", 0.005)),
            )

        ability_keys = self.cfg.get("ability_keys") or []
        self._cycle = itertools.cycle(ability_keys) if ability_keys else None
        self.cfg.setdefault("behavior", {})
        self.cfg["behavior"].setdefault("attack_style", "bot")
        self.cfg["behavior"].setdefault("attack_method", "pixel")
        self.cfg["behavior"].setdefault("eat_mode", "low_hp")
        self.cfg["behavior"].setdefault("loot_mode", "off")
        self.cfg["behavior"].setdefault("loot_use_inv", False)
        self.cfg["behavior"].setdefault("bury_mode", "off")
        self.cfg["behavior"].setdefault("free_require_still", False)
        self.cfg["behavior"].setdefault("quit_on_eat_fail", False)
        self.cfg["behavior"].setdefault("rotate_screen", False)
        self.cfg.setdefault("loot", {})
        self.cfg.setdefault("bury", {})
        self.cfg.setdefault("targeting", {})
        t0 = self.cfg["targeting"]
        t0.setdefault("cyan_avoid_enabled", False)
        t0.setdefault("cyan_avoid_size_px", 100)
        t0.setdefault("cyan_avoid_min_pixels", 60)
        t0.setdefault("rotate_interval_s", [3.0, 10.0])
        t0.setdefault("rotate_hold_s", [0.5, 2.0])
        t0.setdefault("rotate_key", "right")
        t0.setdefault("pre_click_snap_enabled", True)
        t0.setdefault("pre_click_snap_patch_px", 11)
        t0.setdefault("pre_click_min_match_px", 12)
        t0.setdefault("pre_click_max_snap_px", 3)
        t0.setdefault("pre_click_verify_radius_px", 3)
        self._human = human.HumanSession(cfg=dict(self.cfg.get("human") or {}))

        Path("logs").mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._log = open(f"logs/session-{stamp}.jsonl", "a", buffering=1)

    def _behavior(self) -> dict:
        return self.cfg.setdefault("behavior", {})

    def _attack_style(self) -> str:
        style = str(self._behavior().get("attack_style", "bot")).lower()
        return style if style in ("bot", "human", "off") else "bot"

    def _attack_enabled(self) -> bool:
        """False when attack is off — eat and loot still run."""
        return self._attack_style() in ("bot", "human")

    def _attack_method(self) -> str:
        method = str(self._behavior().get("attack_method", "pixel")).lower()
        return method if method in ("pixel", "static", "free") else "pixel"

    def _eat_mode(self) -> str:
        return str(self._behavior().get("eat_mode", "low_hp")).lower()

    def _loot_mode(self) -> str:
        mode = str(self._behavior().get("loot_mode", "off")).lower()
        if mode in ("after-combat", "combat", "target"):
            return "after_combat"
        return mode if mode in ("off", "after_combat", "always") else "off"

    def _loot_use_inv(self) -> bool:
        """True when always-loot should watch mark.py loot button / blank inv."""
        return bool(self._behavior().get("loot_use_inv", False))

    def _bury_mode(self) -> str:
        mode = str(self._behavior().get("bury_mode", "off")).lower()
        if mode in ("after-combat", "combat", "target"):
            return "after_combat"
        return mode if mode in ("off", "after_combat", "always") else "off"

    def _free_require_still(self) -> bool:
        """When False, free mode skips the 2s stillness wait (pre-click verify stays)."""
        return bool(self._behavior().get("free_require_still", False))

    def _quit_on_eat_fail(self) -> bool:
        """When True, eat fail ×N prints kills and fully stops the bot."""
        return bool(self._behavior().get("quit_on_eat_fail", False))

    def _cyan_avoid_enabled(self) -> bool:
        """Pre-click cyan trap reject — only while attack is on."""
        return bool((self.cfg.get("targeting") or {}).get(
            "cyan_avoid_enabled", False))

    def _rotate_screen(self) -> bool:
        """When True, periodically hold Right Arrow to spin the camera."""
        return bool(self._behavior().get("rotate_screen", False))

    def _is_human(self) -> bool:
        return self._attack_style() == "human"

    def _roll_retarget_cooldown(self) -> float:
        """Seconds to wait before the next attack click.

        ``targeting.retarget_cooldown_s`` may be a single number or ``[lo, hi]``
        for a uniform random wait (default 4.5–6.0). Armed after each click
        attempt and again when the target bar clears, so we can watch for an
        auto-acquired second target before clicking elsewhere.
        """
        return self._roll_targeting_range(
            "retarget_cooldown_s", default=(4.5, 6.0))

    def _roll_free_max_bar_hold(self) -> float:
        """Seconds to hold TARGET BAR before force-retarget (free method).

        ``targeting.free_max_bar_hold_s`` may be a number or ``[lo, hi]``.
        """
        return self._roll_targeting_range(
            "free_max_bar_hold_s", default=(30.0, 40.0))

    def _roll_targeting_range(self, key: str, *, default: tuple[float, float]
                              ) -> float:
        """Uniform roll from a targeting scalar or ``[lo, hi]`` pair."""
        raw = (self.cfg.get("targeting") or {}).get(key, default)
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            lo, hi = float(raw[0]), float(raw[1])
            if hi < lo:
                lo, hi = hi, lo
            if hi <= lo:
                return max(0.0, lo)
            return random.uniform(lo, hi)
        return max(0.0, float(raw))

    def _free_max_bar_hold_cfg(self):
        """Config value for overlay (pair or scalar)."""
        return (self.cfg.get("targeting") or {}).get(
            "free_max_bar_hold_s", [30.0, 40.0])

    def _persist_behavior(self) -> None:
        """Write behavior toggles into config.json so they survive restarts."""
        try:
            config_util.patch_live({"behavior": dict(self._behavior())})
        except OSError as exc:
            print(f"WARNING: could not persist behavior toggles: {exc}")

    def _persist_targeting(self) -> None:
        """Persist targeting timer fields."""
        try:
            config_util.patch_live(
                {"targeting": dict(self.cfg.get("targeting") or {})})
        except OSError as exc:
            print(f"WARNING: could not persist targeting: {exc}")

    def _persist_loot(self) -> None:
        try:
            config_util.patch_live({"loot": dict(self.cfg.get("loot") or {})})
        except OSError as exc:
            print(f"WARNING: could not persist loot: {exc}")

    def _persist_bury(self) -> None:
        try:
            config_util.patch_live({"bury": dict(self.cfg.get("bury") or {})})
        except OSError as exc:
            print(f"WARNING: could not persist bury: {exc}")

    @staticmethod
    def _nudge_pair(pair, delta: float, lo_min: float = 0.5,
                    hi_max: float = 30.0) -> list[float]:
        """Shift a [lo, hi] interval by delta, clamped."""
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            lo, hi = float(pair[0]), float(pair[1])
        else:
            lo = hi = float(pair)
        width = max(0.0, hi - lo)
        lo = max(lo_min, min(hi_max - width, lo + delta))
        hi = min(hi_max, lo + width)
        if hi < lo:
            hi = lo
        return [round(lo, 2), round(hi, 2)]

    def _nudge_scalar(self, key: str, delta: float, *,
                      minimum: float, maximum: float, as_int: bool = False
                      ) -> None:
        tcfg = self.cfg.setdefault("targeting", {})
        cur = float(tcfg.get(key, minimum))
        nxt = max(minimum, min(maximum, cur + delta))
        tcfg[key] = int(round(nxt)) if as_int else round(nxt, 2)
        self._persist_targeting()
        self._last_action = f"{key} → {tcfg[key]}"
        print(self._last_action)

    def _toggle_attack_style(self, force: str | None = None) -> None:
        b = self._behavior()
        order = ("bot", "human", "off")
        if force in order:
            b["attack_style"] = force
        else:
            cur = self._attack_style()
            b["attack_style"] = order[(order.index(cur) + 1) % len(order)]
        style = b["attack_style"]
        self._last_action = f"attack style → {style}"
        print(f"Attack style: {style}")
        if style == "human":
            print("  Human: curved clicks + mouse wander while waiting.")
            print("  Keep RuneScape the front window — otherwise it pauses and won't move.")
        elif style == "off":
            print("  Attack off — no targeting clicks or ability keys; eat/loot still work.")
        self._persist_behavior()

    def _toggle_attack_method(self, force: str | None = None) -> None:
        b = self._behavior()
        order = ("pixel", "static", "free")
        if force in order:
            b["attack_method"] = force
        else:
            cur = self._attack_method()
            b["attack_method"] = order[(order.index(cur) + 1) % len(order)]
        method = b["attack_method"]
        self._static.reset()
        self._free.reset()
        self._free_expect_bar = False
        self._free_saw_bar = False
        self._free_bar_on_since = -1e9
        self._free_bar_hold_limit_s = 40.0
        self._free_post_clear_watch = False
        self._free_force_retarget = False
        self._free_need_bar_edge = False
        self._last_action = f"attack method → {method}"
        print(f"Attack method: {method}")
        if method == "static":
            still = float((self.cfg.get("targeting") or {}).get(
                "static_still_s", static_target.DEFAULT_STILL_S))
            print(f"  Static: same pixel blobs, click only if still ~{still:.1f}s.")
        elif method == "free":
            still = float((self.cfg.get("targeting") or {}).get(
                "static_still_s", static_target.DEFAULT_STILL_S))
            if self._free_require_still():
                print("  Free: visible-area colours, no marker/fence; "
                      f"still ~{still:.1f}s + target-bar gates.")
            else:
                print("  Free: visible-area colours, no marker/fence; "
                      "still wait off; target-bar gates + pre-click verify.")
        else:
            print("  Pixel: click densest colour blob (unchanged).")
        self._persist_behavior()

    def _toggle_eat_mode(self, force: str | None = None) -> None:
        b = self._behavior()
        if force in ("low_hp", "in_combat"):
            b["eat_mode"] = force
        else:
            b["eat_mode"] = (
                "in_combat" if self._eat_mode() != "in_combat" else "low_hp")
        mode = b["eat_mode"]
        self._last_action = f"eat mode → {mode}"
        print(f"Eat mode: {mode}")
        self._persist_behavior()

    def _toggle_loot_mode(self, force: str | None = None) -> None:
        b = self._behavior()
        order = ("off", "after_combat", "always")
        if force in order:
            b["loot_mode"] = force
        else:
            cur = self._loot_mode()
            b["loot_mode"] = order[(order.index(cur) + 1) % len(order)]
        mode = b["loot_mode"]
        self._loot_always_gap_s = 0.0
        self._last_action = f"loot mode → {mode}"
        print(f"Loot mode: {mode}")
        if mode == "after_combat":
            print("  Target: Space once when the target bar clears (no inv box).")
        elif mode == "always":
            lo, hi = loot.always_interval_range(self.cfg.get("loot"))
            if self._loot_use_inv():
                if not loot.loot_probes_ready(self.cfg.get("probes")):
                    print("  Inv box on: mark.py → l (button) + i (blank inv)")
                else:
                    print(f"  Always + inv box: Space when loot probes pass "
                          f"(gap {lo:.0f}–{hi:.0f}s random).")
            else:
                print(f"  Always: Space every {lo:.0f}–{hi:.0f}s (random); inv box off.")
        self._persist_behavior()

    def _toggle_loot_use_inv(self, force: bool | None = None) -> None:
        b = self._behavior()
        if force is None:
            b["loot_use_inv"] = not self._loot_use_inv()
        else:
            b["loot_use_inv"] = bool(force)
        on = bool(b["loot_use_inv"])
        self._last_action = f"loot inv box → {'on' if on else 'off'}"
        print(f"Loot inv box: {'on' if on else 'off'}")
        if on:
            print("  Always mode uses mark.py loot button + blank inv probes.")
        else:
            lo, hi = loot.always_interval_range(self.cfg.get("loot"))
            print(f"  Space only — target = after bar clear; "
                  f"always = every {lo:.0f}–{hi:.0f}s random.")
        self._persist_behavior()

    def _toggle_bury_mode(self, force: str | None = None) -> None:
        b = self._behavior()
        order = ("off", "after_combat", "always")
        if force in order:
            b["bury_mode"] = force
        else:
            cur = self._bury_mode()
            b["bury_mode"] = order[(order.index(cur) + 1) % len(order)]
        mode = b["bury_mode"]
        self._bury_always_gap_s = 0.0
        key = str((self.cfg.get("bury") or {}).get("bury_key") or "]")
        self._last_action = f"bury mode → {mode}"
        print(f"Bury mode: {mode} (key={key!r})")
        if mode == "after_combat":
            print("  Target: bury key once when the target bar clears.")
        elif mode == "always":
            lo, hi = bury.always_interval_range(self.cfg.get("bury"))
            print(f"  Always: press {key!r} every {lo:.0f}–{hi:.0f}s (random).")
        self._persist_behavior()

    def _toggle_free_require_still(self, force: bool | None = None) -> None:
        b = self._behavior()
        if force is None:
            b["free_require_still"] = not self._free_require_still()
        else:
            b["free_require_still"] = bool(force)
        on = bool(b["free_require_still"])
        self._static.reset()
        self._last_action = f"free still → {'on' if on else 'off'}"
        print(f"Free still: {'on' if on else 'off'}"
              + (" (wait static_still_s)" if on
                 else " (click ASAP; pre-click pixel verify kept)"))
        self._persist_behavior()

    def _toggle_quit_on_eat_fail(self, force: bool | None = None) -> None:
        b = self._behavior()
        if force is None:
            b["quit_on_eat_fail"] = not self._quit_on_eat_fail()
        else:
            b["quit_on_eat_fail"] = bool(force)
        on = bool(b["quit_on_eat_fail"])
        n = int(self.cfg.get("eat_max_failures", 2))
        self._last_action = f"food quit → {'on' if on else 'off'}"
        print(f"Food quit: {'on' if on else 'off'}"
              + (f" (stop bot after eat fail ×{n})" if on
                 else " (SAFE STOP pause only)"))
        self._persist_behavior()

    def _toggle_cyan_avoid(self, force: bool | None = None) -> None:
        tcfg = self.cfg.setdefault("targeting", {})
        if force is None:
            tcfg["cyan_avoid_enabled"] = not self._cyan_avoid_enabled()
        else:
            tcfg["cyan_avoid_enabled"] = bool(force)
        on = bool(tcfg["cyan_avoid_enabled"])
        size = int(tcfg.get("cyan_avoid_size_px", 100))
        self._last_action = f"cyan avoid → {'on' if on else 'off'}"
        print(f"Cyan avoid: {'on' if on else 'off'}"
              + (f" ({size}×{size}px before click)" if on else ""))
        self._persist_targeting()

    def _toggle_rotate_screen(self, force: bool | None = None) -> None:
        b = self._behavior()
        if force is None:
            b["rotate_screen"] = not self._rotate_screen()
        else:
            b["rotate_screen"] = bool(force)
        on = bool(b["rotate_screen"])
        self._last_action = f"rotate → {'on' if on else 'off'}"
        print(f"Rotate screen: {'on' if on else 'off'}"
              + (" (Right Arrow, random gap/hold)" if on else ""))
        if not on:
            self._rotate_release_now()
        else:
            # First spin after a short random wait — not immediately.
            self._next_rotate_at = time.time() + self._roll_targeting_range(
                "rotate_interval_s", default=(3.0, 10.0))
        self._persist_behavior()

    def _apply_overlay_stepper(self, hit: str) -> None:
        """Handle −/+ overlay hits for free timers and loot/bury gaps."""
        tcfg = self.cfg.setdefault("targeting", {})
        if hit == "cd_dec":
            tcfg["retarget_cooldown_s"] = self._nudge_pair(
                tcfg.get("retarget_cooldown_s", [4.5, 6.0]), -0.5)
            self._persist_targeting()
            self._last_action = f"bar wait → {tcfg['retarget_cooldown_s']}"
        elif hit == "cd_inc":
            tcfg["retarget_cooldown_s"] = self._nudge_pair(
                tcfg.get("retarget_cooldown_s", [4.5, 6.0]), 0.5)
            self._persist_targeting()
            self._last_action = f"bar wait → {tcfg['retarget_cooldown_s']}"
        elif hit == "hard_dec":
            tcfg["free_max_bar_hold_s"] = self._nudge_pair(
                tcfg.get("free_max_bar_hold_s", [30.0, 40.0]), -1.0,
                lo_min=1.0, hi_max=180.0)
            self._persist_targeting()
            self._last_action = f"hard hold → {tcfg['free_max_bar_hold_s']}"
        elif hit == "hard_inc":
            tcfg["free_max_bar_hold_s"] = self._nudge_pair(
                tcfg.get("free_max_bar_hold_s", [30.0, 40.0]), 1.0,
                lo_min=1.0, hi_max=180.0)
            self._persist_targeting()
            self._last_action = f"hard hold → {tcfg['free_max_bar_hold_s']}"
        elif hit == "still_s_dec":
            self._nudge_scalar("static_still_s", -0.5,
                               minimum=0.0, maximum=8.0)
            return
        elif hit == "still_s_inc":
            self._nudge_scalar("static_still_s", 0.5,
                               minimum=0.0, maximum=8.0)
            return
        elif hit == "arm_dec":
            self._nudge_scalar("target_bar_arm_s", -0.5,
                               minimum=0.5, maximum=10.0)
            return
        elif hit == "arm_inc":
            self._nudge_scalar("target_bar_arm_s", 0.5,
                               minimum=0.5, maximum=10.0)
            return
        elif hit == "cyan_sz_dec":
            self._nudge_scalar("cyan_avoid_size_px", -10.0,
                               minimum=40.0, maximum=240.0, as_int=True)
            return
        elif hit == "cyan_sz_inc":
            self._nudge_scalar("cyan_avoid_size_px", 10.0,
                               minimum=40.0, maximum=240.0, as_int=True)
            return
        elif hit == "loot_gap_dec":
            lcfg = self.cfg.setdefault("loot", {})
            lcfg["always_interval_s"] = self._nudge_pair(
                lcfg.get("always_interval_s", [2.0, 5.0]), -0.5,
                lo_min=0.5, hi_max=20.0)
            self._persist_loot()
            self._last_action = f"loot gap → {lcfg['always_interval_s']}"
        elif hit == "loot_gap_inc":
            lcfg = self.cfg.setdefault("loot", {})
            lcfg["always_interval_s"] = self._nudge_pair(
                lcfg.get("always_interval_s", [2.0, 5.0]), 0.5,
                lo_min=0.5, hi_max=20.0)
            self._persist_loot()
            self._last_action = f"loot gap → {lcfg['always_interval_s']}"
        elif hit == "bury_gap_dec":
            bcfg = self.cfg.setdefault("bury", {})
            bcfg["always_interval_s"] = self._nudge_pair(
                bcfg.get("always_interval_s", [2.0, 5.0]), -0.5,
                lo_min=0.5, hi_max=20.0)
            self._persist_bury()
            self._last_action = f"bury gap → {bcfg['always_interval_s']}"
        elif hit == "bury_gap_inc":
            bcfg = self.cfg.setdefault("bury", {})
            bcfg["always_interval_s"] = self._nudge_pair(
                bcfg.get("always_interval_s", [2.0, 5.0]), 0.5,
                lo_min=0.5, hi_max=20.0)
            self._persist_bury()
            self._last_action = f"bury gap → {bcfg['always_interval_s']}"
        elif hit == "rot_gap_dec":
            tcfg["rotate_interval_s"] = self._nudge_pair(
                tcfg.get("rotate_interval_s", [3.0, 10.0]), -0.5,
                lo_min=1.0, hi_max=60.0)
            self._persist_targeting()
            self._last_action = f"rot gap → {tcfg['rotate_interval_s']}"
        elif hit == "rot_gap_inc":
            tcfg["rotate_interval_s"] = self._nudge_pair(
                tcfg.get("rotate_interval_s", [3.0, 10.0]), 0.5,
                lo_min=1.0, hi_max=60.0)
            self._persist_targeting()
            self._last_action = f"rot gap → {tcfg['rotate_interval_s']}"
        elif hit == "rot_hold_dec":
            tcfg["rotate_hold_s"] = self._nudge_pair(
                tcfg.get("rotate_hold_s", [0.5, 2.0]), -0.1,
                lo_min=0.2, hi_max=5.0)
            self._persist_targeting()
            self._last_action = f"rot hold → {tcfg['rotate_hold_s']}"
        elif hit == "rot_hold_inc":
            tcfg["rotate_hold_s"] = self._nudge_pair(
                tcfg.get("rotate_hold_s", [0.5, 2.0]), 0.1,
                lo_min=0.2, hi_max=5.0)
            self._persist_targeting()
            self._last_action = f"rot hold → {tcfg['rotate_hold_s']}"
        else:
            return
        print(self._last_action)

    def _on_overlay_mouse(self, event, x, y, flags, param) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        hit = overlay.hit_test(x, y)
        if hit is None:
            return
        if hit == "attack_bot":
            self._toggle_attack_style("bot")
        elif hit == "attack_human":
            self._toggle_attack_style("human")
        elif hit == "attack_off":
            self._toggle_attack_style("off")
        elif hit == "method_pixel":
            self._toggle_attack_method("pixel")
        elif hit == "method_static":
            self._toggle_attack_method("static")
        elif hit == "method_free":
            self._toggle_attack_method("free")
        elif hit == "eat_low_hp":
            self._toggle_eat_mode("low_hp")
        elif hit == "eat_in_combat":
            self._toggle_eat_mode("in_combat")
        elif hit == "loot_off":
            self._toggle_loot_mode("off")
        elif hit == "loot_after_combat":
            self._toggle_loot_mode("after_combat")
        elif hit == "loot_always":
            self._toggle_loot_mode("always")
        elif hit == "loot_inv_off":
            self._toggle_loot_use_inv(False)
        elif hit == "loot_inv_on":
            self._toggle_loot_use_inv(True)
        elif hit == "bury_off":
            self._toggle_bury_mode("off")
        elif hit == "bury_after_combat":
            self._toggle_bury_mode("after_combat")
        elif hit == "bury_always":
            self._toggle_bury_mode("always")
        elif hit == "free_still_off":
            self._toggle_free_require_still(False)
        elif hit == "free_still_on":
            self._toggle_free_require_still(True)
        elif hit == "food_quit_off":
            self._toggle_quit_on_eat_fail(False)
        elif hit == "food_quit_on":
            self._toggle_quit_on_eat_fail(True)
        elif hit == "cyan_avoid_off":
            self._toggle_cyan_avoid(False)
        elif hit == "cyan_avoid_on":
            self._toggle_cyan_avoid(True)
        elif hit == "rotate_off":
            self._toggle_rotate_screen(False)
        elif hit == "rotate_on":
            self._toggle_rotate_screen(True)
        elif hit.endswith("_dec") or hit.endswith("_inc"):
            self._apply_overlay_stepper(hit)
        else:
            return
        self._overlay_dirty = True

    def _overlay_render_kwargs(self, *, hp: float, adren: float,
                               fighting: bool, reason: str, score: float,
                               status: str, status_colour, eat_threshold: float,
                               ability_keys: list, target_count: int,
                               click_in_s: float | None, probe_mode: bool,
                               under_attack: bool, now: float) -> dict:
        """Build overlay.render kwargs from live cfg + latest vitals."""
        tcfg = self.cfg.get("targeting") or {}
        lcfg = self.cfg.get("loot") or {}
        bcfg = self.cfg.get("bury") or {}
        return dict(
            hp=hp, adren=adren, fighting=fighting, reason=reason,
            score=score, status=status, status_colour=status_colour,
            last_action=self._last_action, eat_threshold=eat_threshold,
            dry_run=self.dry_run, ability_keys=ability_keys,
            eat_suspended=self._eat_suspended,
            eat_failures=self._eat_failures,
            target_count=target_count,
            click_in_s=click_in_s,
            attack_style=self._attack_style(),
            attack_method=self._attack_method(),
            eat_mode=self._eat_mode(),
            loot_mode=self._loot_mode(),
            loot_use_inv=self._loot_use_inv(),
            bury_mode=self._bury_mode(),
            free_require_still=self._free_require_still(),
            quit_on_eat_fail=self._quit_on_eat_fail(),
            cyan_avoid_enabled=self._cyan_avoid_enabled(),
            cyan_avoid_size_px=float(tcfg.get("cyan_avoid_size_px", 100)),
            rotate_screen=self._rotate_screen(),
            rotate_interval_s=tcfg.get("rotate_interval_s", [3.0, 10.0]),
            rotate_hold_s=tcfg.get("rotate_hold_s", [0.5, 2.0]),
            retarget_cooldown_s=tcfg.get("retarget_cooldown_s", [4.5, 6.0]),
            free_max_bar_hold_s=self._free_max_bar_hold_cfg(),
            static_still_s=float(tcfg.get(
                "static_still_s", static_target.DEFAULT_STILL_S)),
            target_bar_arm_s=float(tcfg.get("target_bar_arm_s", 3.0)),
            loot_interval_s=lcfg.get("always_interval_s", [2.0, 5.0]),
            bury_interval_s=bcfg.get("always_interval_s", [2.0, 5.0]),
            probe_mode=probe_mode,
            under_attack=under_attack,
            kills=self._kills,
            kills_per_hour=self._kills_per_hour(now),
        )

    def _redraw_overlay_now(self) -> None:
        """Immediate panel refresh after a toggle/stepper click (no input pump)."""
        if not self._overlay_ready or not self._overlay_cache:
            return
        base = self._overlay_cache
        kw = self._overlay_render_kwargs(
            hp=float(base.get("hp", 1.0)),
            adren=float(base.get("adren", 0.0)),
            fighting=bool(base.get("fighting", False)),
            reason=str(base.get("reason", "")),
            score=float(base.get("score", 0.0)),
            status=str(base.get("status", "RUNNING")),
            status_colour=base.get("status_colour", (90, 235, 90)),
            eat_threshold=float(base.get("eat_threshold", 0.5)),
            ability_keys=list(base.get("ability_keys") or []),
            target_count=int(base.get("target_count", 0)),
            click_in_s=base.get("click_in_s"),
            probe_mode=bool(base.get("probe_mode", False)),
            under_attack=bool(base.get("under_attack", False)),
            now=time.time(),
        )
        cv2.imshow("RS3 bot", overlay.render(**kw))
        self._overlay_cache = kw
        self._overlay_dirty = False

    def _pump_overlay_idle(self, seconds: float) -> None:
        """Sleep while polling overlay clicks — only for tick-idle remainder.

        Does not run during mouse-move / eat delays (that path made the bot
        crawl when waitKey was pumped inside every action).
        """
        if not self._overlay_ready or seconds <= 0:
            if seconds > 0:
                time.sleep(seconds)
            return
        deadline = time.monotonic() + float(seconds)
        while not self.stop:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                self.stop = True
                break
            if self._overlay_dirty:
                self._redraw_overlay_now()
            time.sleep(min(0.05, left))

    # ---------- reading ----------

    def read(self, win, frame: np.ndarray | None = None
             ) -> tuple[float, float, np.ndarray, np.ndarray, bool, bool, str, float]:
        """Return hp, adren, ad_img, tx_img, fighting, under_attack, reason, score.

        When probes are configured, ``frame`` (full window) is required for
        accurate signals; a fresh grab is taken if omitted.
        """
        if self._use_probes:
            if frame is None:
                frame = self._grab.grab(win.region)
            sig = probes.read_signals(frame, self.cfg.get("probes"))
            # Tiny placeholders keep the old CombatDetector signature happy.
            blank = np.zeros((4, 4, 3), dtype=np.uint8)
            self._combat.last_reason = sig["reason"]
            self._combat.last_score = 0.0
            return (
                float(sig["hp"]),
                float(sig["adren"]),
                blank,
                blank,
                bool(sig["fighting"]),
                bool(sig["under_attack"]),
                str(sig["reason"]),
                0.0,
            )

        r = self.cfg["regions"]
        hp_img = self._grab.grab(win.frac_to_region(r["hp_bar"]))
        ad_img = self._grab.grab(win.frac_to_region(r["adren_bar"]))
        tx_img = self._grab.grab(win.frac_to_region(r["adren_text"]))
        hp = bars.health_fraction(hp_img, self.cfg.get("hp_inner_width"))
        ad = bars.adrenaline_fraction(ad_img)
        return hp, ad, ad_img, tx_img, False, False, "", 0.0

    # ---------- acting ----------

    def _press(self, key: str, win, why: str) -> None:
        if self.dry_run:
            self._last_action = f"[dry run] would press {key} ({why})"
            return
        mode = self.cfg.get("key_mode", "hid")
        if self._is_human():
            broke = self._human.maybe_break()
            if broke is not None:
                note, dur = broke
                self._last_action = note
                # Mute loot/bury for this tick and a short quiet window after.
                self._skip_support_keys_once = True
                self._support_pause_until = max(
                    self._support_pause_until, time.time() + max(0.0, dur))
                print(f"{note} — loot/bury paused")
            human.press_human(key, self.cfg.get("human"), mode=mode, pid=win.pid)
        else:
            keys.press(key, mode=mode, pid=win.pid)
        self._last_action = f"pressed {key} ({why}) at {datetime.now():%H:%M:%S}"

    def _press_human_key(self, key: str, win, why: str) -> None:
        """Always use human key timing (eat + loot/bury), regardless of attack_style."""
        if self.dry_run:
            self._last_action = f"[dry run] would press {key} ({why})"
            return
        mode = self.cfg.get("key_mode", "hid")
        human.press_human(key, self.cfg.get("human"), mode=mode, pid=win.pid)
        self._last_action = f"pressed {key} ({why}) at {datetime.now():%H:%M:%S}"

    def _support_keys_paused(self, now: float) -> bool:
        """True when Space / bury must not fire (rest, cooldown, or SAFE STOP)."""
        if self._eat_suspended:
            return True
        if self._skip_support_keys_once:
            return True
        if now < self._support_pause_until:
            return True
        # Post-click / post-clear / force-delay waits — do not spam loot/bury.
        if now < self._retarget_after:
            return True
        return False

    def _target_pixel_still_ok(self, x: int, y: int, win,
                               frame: np.ndarray | None = None) -> bool:
        """True if zombie colour is still under screen ``(x, y)``."""
        if frame is None:
            frame = self._grab.grab(win.region)
        lx, ly = int(x - win.x), int(y - win.y)
        tcfg = self.cfg.get("targeting") or {}
        radius = int(tcfg.get("pre_click_verify_radius_px", 3))
        return targets.zombie_colour_under(
            frame, lx, ly, self.cfg, radius_px=radius)

    def _cyan_trap_near(self, x: int, y: int, win,
                        frame: np.ndarray | None = None) -> bool:
        """True if cyan trap colour is near screen ``(x, y)``."""
        if frame is None:
            frame = self._grab.grab(win.region)
        lx, ly = int(x - win.x), int(y - win.y)
        return targets.cyan_near(frame, lx, ly, self.cfg)

    def _pre_click_refine_aim(self, x: int, y: int, win
                              ) -> tuple[int, int] | None:
        """Cyan → snap/min-match → final pixel check. Returns screen aim or None.

        Order is fixed for accuracy: optional cyan trap, then colour snap /
        min-match on the same grab, then a **fresh** grab for the last pixel
        check whenever the aim moved. Mouse-down must use the returned point.
        """
        self._click_abort_reason = ""
        mode = self.cfg.get("key_mode", "hid")
        frame1 = self._grab.grab(win.region)
        if (self._attack_enabled() and self._cyan_avoid_enabled()
                and self._cyan_trap_near(x, y, win, frame=frame1)):
            self._click_abort_reason = "cyan_trap"
            return None

        lx, ly = int(x - win.x), int(y - win.y)
        snapped = targets.snap_aim_to_colour(frame1, lx, ly, self.cfg)
        if snapped is None:
            self._click_abort_reason = "pixel_thin"
            return None
        nx_l, ny_l, _count = snapped
        nx = int(win.x) + nx_l
        ny = int(win.y) + ny_l
        moved = (nx != int(x) or ny != int(y))
        if moved:
            keys.move_to(nx, ny, mode=mode, pid=win.pid)
            time.sleep(0.008)
            frame2 = self._grab.grab(win.region)
        else:
            frame2 = frame1

        # Last gate before click — never skip.
        if not self._target_pixel_still_ok(nx, ny, win, frame=frame2):
            self._click_abort_reason = "pixel_gone"
            return None
        return nx, ny

    def _click(self, x: int, y: int, win, why: str) -> bool:
        """Move to target, cyan/snap/pixel-verify, then click the refined aim.

        Returns True if the click was sent (or dry-run). False if a pre-click
        check failed — caller should pick another target.
        """
        self._click_abort_reason = ""
        if self.dry_run:
            self._last_action = f"[dry run] would click ({x},{y}) ({why})"
            return True
        mode = self.cfg.get("key_mode", "hid")
        if self._is_human():
            # Never break/fidget before a target click — delay = NPC walked away.
            aim_box: list[int] = [x, y]

            def _refine() -> tuple[int, int] | None:
                got = self._pre_click_refine_aim(aim_box[0], aim_box[1], win)
                if got is not None:
                    aim_box[0], aim_box[1] = got
                return got

            ok = human.click_human(
                x, y, self.cfg.get("human"), mode=mode, pid=win.pid,
                pre_click_refine=_refine,
            )
            if not ok:
                reason = self._click_abort_reason or "pixel_gone"
                self._last_action = (
                    f"ABORT click — {reason} at ({x},{y}) ({why})")
                return False
            x, y = aim_box[0], aim_box[1]
        else:
            keys.move_to(x, y, mode=mode, pid=win.pid)
            time.sleep(0.015)
            got = self._pre_click_refine_aim(x, y, win)
            if got is None:
                reason = self._click_abort_reason or "pixel_gone"
                self._last_action = (
                    f"ABORT click — {reason} at ({x},{y}) ({why})")
                return False
            x, y = got
            keys.click_down_up(x, y, mode=mode, pid=win.pid)
        self._last_action = f"clicked ({x},{y}) ({why}) at {datetime.now():%H:%M:%S}"
        return True

    def _update_eat_result(self, hp: float) -> None:
        """Score the last eat attempt once the cooldown window has passed."""
        if not self._awaiting_eat_result or self._hp_at_eat is None:
            return
        if self._use_probes:
            # Success = health pixel returned to baseline (hp ≈ 1.0).
            recovered = hp >= 0.5
        else:
            delta = self.cfg.get("eat_success_delta_pct", 0.5) / 100.0
            recovered = hp >= self._hp_at_eat + delta
        if recovered:
            self._eat_failures = 0
            self._awaiting_eat_result = False
            self._hp_at_eat = None
            return
        self._eat_failures += 1
        self._awaiting_eat_result = False
        self._hp_at_eat = None
        max_fails = int(self.cfg.get("eat_max_failures", 2))
        if self._eat_failures >= max_fails:
            self._eat_suspended = True
            self._eat_resume_ok_since = None
            if self._quit_on_eat_fail():
                now = time.time()
                elapsed_m = 0.0
                if self._session_started_at > 0.0:
                    elapsed_m = (now - self._session_started_at) / 60.0
                kph = self._kills_per_hour(now)
                self._last_action = (
                    f"SAFE STOP: eat failed x{self._eat_failures} — quitting")
                print(self._last_action)
                self._print_session_kills()
                try:
                    self._log.write(json.dumps({
                        "t": round(now, 3),
                        "action": "eat_fail_quit",
                        "eat_failures": self._eat_failures,
                        "kills": self._kills,
                        "kills_per_hour": round(kph, 1),
                        "elapsed_m": round(elapsed_m, 2),
                    }) + "\n")
                except OSError:
                    pass
                self.stop = True
            else:
                self._last_action = (
                    f"SAFE STOP: eat failed x{self._eat_failures} "
                    f"— loot/bury/combat paused")
                print(self._last_action)

    def _maybe_resume_eat(self, hp: float, threshold: float, now: float) -> None:
        """Resume eat/loot/combat only after HP stays OK for a short hold."""
        if not self._eat_suspended:
            self._eat_resume_ok_since = None
            return
        ok = hp >= 0.5 if self._use_probes else hp >= threshold
        hold_s = float(self.cfg.get("eat_resume_hold_s", 1.5))
        if not ok:
            self._eat_resume_ok_since = None
            return
        if self._eat_resume_ok_since is None:
            self._eat_resume_ok_since = now
            return
        if (now - self._eat_resume_ok_since) < hold_s:
            return
        self._eat_suspended = False
        self._eat_failures = 0
        self._eat_resume_ok_since = None
        self._last_action = "eat resumed — loot/bury/combat unpaused"
        print(self._last_action)

    def decide(self, hp: float, fighting: bool, now: float, win,
               under_attack: bool = False) -> str | None:
        food_key = self.cfg.get("food_key")
        threshold = self.cfg["eat_below_pct"] / 100.0
        self._maybe_resume_eat(hp, threshold, now)

        # Only judge the previous eat once the cooldown has elapsed.
        if (self._awaiting_eat_result
                and (now - self._last_eat) >= self.cfg["eat_cooldown_s"]):
            self._update_eat_result(hp)

        if self._eat_suspended:
            return None

        if self._use_probes:
            need_eat = food_key and hp < 0.5
        else:
            need_eat = food_key and hp < threshold
        if need_eat and self._eat_mode() == "in_combat":
            need_eat = under_attack or fighting
        if need_eat and (now - self._last_eat) >= self.cfg["eat_cooldown_s"]:
            self._hp_at_eat = hp
            self._awaiting_eat_result = True
            self._last_eat = now
            if self._use_probes:
                why = "health probe changed" + (
                    " (in combat)" if self._eat_mode() == "in_combat" else "")
            else:
                why = (f"health {hp*100:.0f}% < {self.cfg['eat_below_pct']}%"
                       + (" (in combat)" if self._eat_mode() == "in_combat" else ""))
            self._press_human_key(food_key, win, why)
            return food_key

        if not self._attack_enabled():
            return None

        gcd = float(self.cfg["global_cooldown_s"])
        if self._is_human():
            # Slight human variance on ability pacing.
            hcfg = self.cfg.get("human") or {}
            pair = hcfg.get("ability_gcd_jitter_s") or [-0.15, 0.35]
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                gcd = max(0.4, gcd + random.uniform(float(pair[0]), float(pair[1])))
        if (self._cycle and fighting
                and (now - self._last_ability) >= gcd):
            key = next(self._cycle)
            self._last_ability = now
            self._press(key, win, "fighting")
            return key
        return None

    def _note_target_bar(self, under_attack: bool, now: float) -> None:
        """Track target-bar falling edge for loot window + kill counter."""
        if self._loot_bar_was_on and not under_attack:
            self._loot_bar_cleared_at = now
            self._kills += 1
        self._loot_bar_was_on = under_attack

    def _kills_per_hour(self, now: float) -> float:
        """Session kill rate from bar-clear count (O(1), overlay only)."""
        started = self._session_started_at
        if started <= 0.0 or self._kills <= 0:
            return 0.0
        elapsed_h = max((now - started) / 3600.0, 1.0 / 60.0)
        return self._kills / elapsed_h

    def _print_session_kills(self, *, prefix: str = "Session kills") -> None:
        """Print kills once per session (food-quit path or clean stop)."""
        if self._session_summary_printed:
            return
        now = time.time()
        elapsed_m = 0.0
        if self._session_started_at > 0.0:
            elapsed_m = (now - self._session_started_at) / 60.0
        kph = self._kills_per_hour(now)
        print(f"{prefix}: {self._kills}  ({kph:.0f}/hr over {elapsed_m:.1f}m)")
        self._session_summary_printed = True

    def _maybe_loot(self, frame: np.ndarray, win, now: float) -> str | None:
        """Press Space (only) with human timing when loot mode says so."""
        if self._support_keys_paused(now):
            return None
        mode = self._loot_mode()
        if mode == "off":
            return None

        use_inv = self._loot_use_inv()
        if (mode == "always" and use_inv
                and not loot.loot_probes_ready(self.cfg.get("probes"))):
            if not self._warned_loot_probes:
                print("Loot inv box on: mark.py l (button) + i (blank inv); "
                      "after-combat Space still works as backup.")
                self._warned_loot_probes = True

        # One Space per target-bar clear for after_combat, and for always's
        # after-combat backup when inv probes are on.
        clear_handled = (
            self._loot_bar_cleared_at is not None
            and self._loot_handled_clear_at == self._loot_bar_cleared_at)

        decision = loot.should_loot(
            frame,
            self.cfg.get("probes"),
            self.cfg.get("loot"),
            mode,
            now=now,
            last_loot_at=self._last_loot_at,
            bar_cleared_at=(
                None if clear_handled and mode in ("after_combat", "always")
                else self._loot_bar_cleared_at),
            use_inv=use_inv,
            always_gap_s=self._loot_always_gap_s,
        )
        if not decision.should_loot:
            return None

        is_clear_loot = decision.needs_post_combat_delay or mode == "after_combat"

        lcfg = self.cfg.get("loot") or {}
        if decision.needs_post_combat_delay:
            pair = lcfg.get("post_combat_delay_s") or [0.25, 0.75]
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                delay = random.uniform(float(pair[0]), float(pair[1]))
            else:
                delay = 0.4
            if not self.dry_run:
                time.sleep(delay)

        pick = str(lcfg.get("loot_key") or "space").strip().lower()
        if pick not in keys.KEYCODES:
            pick = "space"
        self._press_human_key(pick, win, decision.reason)
        self._last_loot_at = time.time()
        if mode == "always":
            self._loot_always_gap_s = loot.roll_always_interval(lcfg)
        if is_clear_loot and self._loot_bar_cleared_at is not None:
            self._loot_handled_clear_at = self._loot_bar_cleared_at
        return pick

    def _maybe_bury(self, win, now: float) -> str | None:
        """Press bury key (]) with human timing when bury mode says so."""
        if self._support_keys_paused(now):
            return None
        mode = self._bury_mode()
        if mode == "off":
            return None

        clear_handled = (
            self._loot_bar_cleared_at is not None
            and self._bury_handled_clear_at == self._loot_bar_cleared_at)

        decision = bury.should_bury(
            self.cfg.get("bury"),
            mode,
            now=now,
            last_bury_at=self._last_bury_at,
            bar_cleared_at=(
                None if clear_handled and mode in ("after_combat", "always")
                else self._loot_bar_cleared_at),
            always_gap_s=self._bury_always_gap_s,
        )
        if not decision.should_bury:
            return None

        is_clear_bury = decision.needs_post_combat_delay or mode == "after_combat"

        bcfg = self.cfg.get("bury") or {}
        if decision.needs_post_combat_delay:
            pair = bcfg.get("post_combat_delay_s") or [0.25, 0.75]
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                delay = random.uniform(float(pair[0]), float(pair[1]))
            else:
                delay = 0.4
            if not self.dry_run:
                time.sleep(delay)

        pick = str(bcfg.get("bury_key") or "]").strip().lower()
        if pick not in keys.KEYCODES:
            pick = "]"
        self._press_human_key(pick, win, decision.reason)
        self._last_bury_at = time.time()
        if mode == "always":
            self._bury_always_gap_s = bury.roll_always_interval(bcfg)
        if is_clear_bury and self._loot_bar_cleared_at is not None:
            self._bury_handled_clear_at = self._loot_bar_cleared_at
        return pick

    def _rotate_key_name(self) -> str:
        key = str((self.cfg.get("targeting") or {}).get(
            "rotate_key", "right")).strip().lower()
        return key if key in keys.KEYCODES else "right"

    def _rotate_release_now(self, win=None) -> None:
        """Ensure Right Arrow is up (safe if already released)."""
        if not self._rotate_held:
            return
        if not self.dry_run and win is not None:
            mode = self.cfg.get("key_mode", "hid")
            try:
                keys.key_up(self._rotate_key_name(), mode=mode, pid=win.pid)
            except keys.KeySenderError:
                pass
        self._rotate_held = False
        self._rotate_release_at = -1e9

    def _maybe_rotate(self, win, now: float) -> str | None:
        """Non-blocking camera spin: Right Arrow down → hold → up.

        Gap between spins: ``targeting.rotate_interval_s`` (default 3–10s).
        Hold duration: ``targeting.rotate_hold_s`` (default 0.5–2s).
        Skips while paused / not frontmost / eat SAFE STOP; never blocks the
        main loop for the hold duration.
        """
        if self._rotate_held:
            if now >= self._rotate_release_at:
                if self.dry_run:
                    self._last_action = (
                        f"[dry run] would release {self._rotate_key_name()} "
                        "(rotate)")
                elif win is not None:
                    mode = self.cfg.get("key_mode", "hid")
                    keys.key_up(self._rotate_key_name(), mode=mode, pid=win.pid)
                self._rotate_held = False
                gap = self._roll_targeting_range(
                    "rotate_interval_s", default=(3.0, 10.0))
                self._next_rotate_at = now + gap
                self._last_action = (
                    f"rotate release → next in {gap:.1f}s")
                return "rotate_up"
            return None

        if not self._rotate_screen():
            return None
        if win is None or self.paused:
            return None
        if self._eat_suspended:
            return None
        if now < self._next_rotate_at:
            return None

        hold = self._roll_targeting_range(
            "rotate_hold_s", default=(0.5, 2.0))
        key = self._rotate_key_name()
        if self.dry_run:
            self._last_action = (
                f"[dry run] would hold {key} {hold:.1f}s (rotate)")
        else:
            mode = self.cfg.get("key_mode", "hid")
            keys.key_down(key, mode=mode, pid=win.pid)
            self._last_action = f"rotate hold {key} {hold:.1f}s"
        self._rotate_held = True
        self._rotate_release_at = now + hold
        return "rotate_down"

    def _free_pixel_tick(self, win, now: float, show_aoi: bool,
                         hp: float = 1.0,
                         frame: np.ndarray | None = None
                         ) -> tuple[int, float | None]:
        """Visible-area colour clicks with optional stillness + target-bar gates.

        No cyan marker / fence. Colour-match clusters (free_min_match_area).
        When ``free_require_still`` is off, raw matches are clickable; when on,
        only still ones count. Pre-click verify stays.
        """
        tcfg = self.cfg.get("targeting") or {}
        if not tcfg.get("enabled", False):
            self._target_count = 0
            self._last_pick = None
            return 0, None
        if not (tcfg.get("zombie_colors_bgr") or []):
            self._target_count = 0
            self._last_pick = None
            if show_aoi and not getattr(self, "_warned_no_colors", False):
                print("No zombie colours yet. Run:  python3 mark.py  (press z)")
                self._warned_no_colors = True
            return 0, None

        if frame is None:
            frame = self._grab.grab(win.region)

        raw = free_pixel.find_visible_targets(frame, win, self.cfg)
        # Free mode: same stillness idea, but tolerate blob-centroid jitter more
        # so standing NPCs actually count as still. Optional: skip still entirely.
        if self._free_require_still():
            still_cfg = dict(tcfg)
            still_cfg.setdefault("static_match_radius_px", 48)
            still_cfg.setdefault("static_move_frac", 0.55)
            still_cfg.setdefault(
                "static_still_s",
                float(tcfg.get("static_still_s", static_target.DEFAULT_STILL_S)))
            found = self._static.filter_targets(raw, now, still_cfg)
        else:
            found = raw
        self._target_count = len(found)

        arm_s = float(tcfg.get("target_bar_arm_s", 3.0))
        force_skip_r = int(tcfg.get("force_retarget_skip_radius_px", 100))
        since_click = now - self._clicked_at
        waiting_for_bar = (
            self._free_expect_bar and not self._free_saw_bar
            and since_click < arm_s
            and not self._free_force_retarget
            and not self._free_need_bar_edge)

        if self._use_probes:
            bar_raw = probes.color_matches(
                frame,
                (self.cfg.get("probes") or {}).get("target_bar") or {},
                probes.tolerance(self.cfg.get("probes")),
            ) if probes.has_probe(self.cfg.get("probes"), "target_bar") else False
        else:
            bar_raw = combat.target_bar_visible(frame, self.cfg.get("target_bar"))

        cooling = now < self._retarget_after

        # Engagement-scoped bar gate: ignore a sticky bar probe while idle.
        # Hard-limit: bar stayed on too long → force a DIFFERENT still NPC soon.
        # Do not open post-clear watch (that re-latches while bar_raw is still True).
        if self._free_need_bar_edge:
            if not bar_raw:
                # Bar finally dropped — next ON starts a normal engagement timer.
                self._free_need_bar_edge = False
            elif (not self._free_force_retarget
                  and not cooling
                  and (now - self._clicked_at) >= 2.0):
                # Force click did not clear sticky bar — try another NPC.
                self._free_force_retarget = True
                self._free_expect_bar = False
                self._free_saw_bar = False
                self._retarget_after = now  # click ASAP

        if self._free_expect_bar and not self._free_force_retarget:
            if bar_raw and not self._free_need_bar_edge:
                if not self._free_saw_bar:
                    self._free_saw_bar = True
                    self._free_bar_on_since = now
                    self._free_bar_hold_limit_s = self._roll_free_max_bar_hold()
                    self._free_post_clear_watch = False
                hold_limit = self._free_bar_hold_limit_s
                if (now - self._free_bar_on_since) >= hold_limit:
                    self._free_expect_bar = False
                    self._free_saw_bar = False
                    self._free_post_clear_watch = False
                    self._free_force_retarget = True
                    # Short delay only — full bar-wait watch is for real bar-clear.
                    delay = tcfg.get("free_force_delay_s", [0.25, 0.7])
                    if isinstance(delay, (list, tuple)) and len(delay) == 2:
                        lo, hi = float(delay[0]), float(delay[1])
                        if hi < lo:
                            lo, hi = hi, lo
                        wait = random.uniform(lo, hi) if hi > lo else lo
                    else:
                        wait = 0.4
                    self._retarget_after = now + max(0.0, wait)
                    print(
                        f"FREE hard-limit {hold_limit:.1f}s — "
                        f"forcing different target in {wait:.1f}s")
            elif self._free_saw_bar and not self._free_need_bar_edge:
                # Fight ended — watch for auto-next target before clicking.
                self._free_expect_bar = False
                self._free_saw_bar = False
                self._free_post_clear_watch = True
                self._retarget_after = now + self._roll_retarget_cooldown()
            elif since_click >= arm_s and not self._free_need_bar_edge:
                self._free_expect_bar = False
                self._free_saw_bar = False

        cooling = now < self._retarget_after

        # Bar reappeared during post-clear watch (bar had gone off first).
        if (self._free_post_clear_watch and bar_raw
                and not self._free_expect_bar
                and not self._free_force_retarget):
            self._free_expect_bar = True
            self._free_saw_bar = True
            self._free_bar_on_since = now
            self._free_bar_hold_limit_s = self._roll_free_max_bar_hold()
            self._free_post_clear_watch = False

        bar_holding = bool(
            self._free_expect_bar and self._free_saw_bar and bar_raw
            and not self._free_force_retarget
            and not self._free_need_bar_edge)

        retarget_pct = float(self.cfg.get(
            "retarget_above_hp_pct",
            self.cfg.get("eat_below_pct", 50)))
        hp_ok = (hp >= 0.5) if self._use_probes else hp > (retarget_pct / 100.0)
        click_in: float | None = None
        pool = found
        hold_limit = self._free_bar_hold_limit_s

        if bar_holding:
            allow_click = False
            gate = "TARGET BAR"
            click_in = max(0.0, hold_limit - (now - self._free_bar_on_since))
        elif waiting_for_bar:
            allow_click = False
            gate = "WAIT BAR"
            click_in = max(0.0, arm_s - since_click)
        elif cooling:
            allow_click = False
            gate = "COOLDOWN"
            click_in = max(0.0, self._retarget_after - now)
        elif not hp_ok:
            allow_click = False
            gate = "WAIT HP"
        else:
            if (self._free_post_clear_watch and bar_raw
                    and not self._free_force_retarget):
                self._free_expect_bar = True
                self._free_saw_bar = True
                self._free_bar_on_since = now
                self._free_bar_hold_limit_s = self._roll_free_max_bar_hold()
                self._free_post_clear_watch = False
                allow_click = False
                gate = "TARGET BAR"
                click_in = self._free_bar_hold_limit_s
            else:
                if self._free_post_clear_watch:
                    self._free_post_clear_watch = False
                allow_click = True
                gate = "READY" if found else "WAIT TARGET"
                if self._free_require_still() and not found and raw:
                    gate = "WAIT STILL"
                if self._free_force_retarget:
                    gate = "FORCE RETRY" if found else gate

                # Skip last click / abort spot. Force path uses a wider skip so
                # nearest-to-center does not re-click the same NPC forever.
                # Cyan-trap aborts also use the avoid box size as skip radius.
                if self._miss_xy is not None and found:
                    mx, my = self._miss_xy
                    if self._click_abort_reason == "cyan_trap":
                        skip_r = int(tcfg.get("cyan_avoid_size_px", 100))
                    elif self._free_force_retarget:
                        skip_r = force_skip_r
                    else:
                        skip_r = int(tcfg.get("miss_skip_radius_px", 55))
                    skip_r2 = skip_r * skip_r
                    others = [t for t in found
                              if (t.x - mx) ** 2 + (t.y - my) ** 2 > skip_r2]
                    if others:
                        pool = others
                        if self._click_abort_reason == "cyan_trap":
                            gate = "CYAN SKIP"
                        elif not self._free_force_retarget:
                            gate = "RETRY"
                    elif self._free_force_retarget and len(found) >= 1:
                        # No far-enough blob — pick farthest from last click.
                        pool = [max(
                            found,
                            key=lambda t: (t.x - mx) ** 2 + (t.y - my) ** 2)]
                        gate = "FORCE RETRY"
                    elif (self._click_abort_reason == "cyan_trap"
                          and len(found) >= 1):
                        pool = [max(
                            found,
                            key=lambda t: (t.x - mx) ** 2 + (t.y - my) ** 2)]
                        gate = "CYAN SKIP"

        if self._eat_suspended:
            allow_click = False
            gate = "EAT STOP"

        pick = free_pixel.pick_target(pool, win, self.cfg)
        self._last_pick = (pick.x, pick.y) if pick else None

        status = f"FREE {gate}  raw={len(raw)} still={len(found)}"
        if gate in ("WAIT BAR", "COOLDOWN", "TARGET BAR") and click_in is not None:
            status = (
                f"FREE {gate} {click_in:.1f}s  raw={len(raw)} still={len(found)}")
        if gate == "EAT STOP":
            status = (
                f"SAFE STOP eat failed — loot/bury/combat paused  "
                f"raw={len(raw)} still={len(found)}")
        self._last_action = status

        # Do not wander during force-retarget cooldown — it delays the click.
        if (self._is_human()
                and gate in ("TARGET BAR", "WAIT BAR", "COOLDOWN")
                and not self._free_force_retarget):
            self._human.cfg = dict(self.cfg.get("human") or {})
            pad = 80
            self._human.wait_wander(
                (win.x + pad, win.y + pad,
                 win.x + win.width - pad, win.y + int(win.height * 0.75)),
                mode=self.cfg.get("key_mode", "hid"),
                pid=win.pid,
            )

        if show_aoi:
            vis = free_pixel.draw_preview(frame, win, raw, pick, status)
            mcfg = aoi._marker_cfg(self.cfg.get("aoi") or {})
            search = aoi.resolve_search_rect(mcfg, win.width, win.height)
            if search is not None:
                x0, y0, x1, y1 = search
                cv2.rectangle(vis, (x0, y0), (x1 - 1, y1 - 1), (255, 180, 0), 2)
            max_w = 900
            if vis.shape[1] > max_w:
                scale = max_w / vis.shape[1]
                vis = cv2.resize(vis, None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_AREA)
            cv2.imshow("RS3 AOI", vis)

        if pick is None or not allow_click or not self._attack_enabled():
            return self._target_count, click_in

        why = f"free nearest of {len(found)} (area={pick.area})"
        was_force = self._free_force_retarget
        clicked = self._click(pick.x, pick.y, win, why)
        if not clicked:
            # Target walked off / cyan trap — skip this spot, retry next tick.
            self._miss_xy = (pick.x, pick.y)
            reason = self._click_abort_reason or "pixel_gone"
            self._log.write(json.dumps({
                "t": round(now, 3), "action": "click_abort",
                "x": pick.x, "y": pick.y, "reason": reason,
                "mode": "free", "dry_run": self.dry_run}) + "\n")
            return self._target_count, click_in

        self._miss_xy = (pick.x, pick.y)
        self._clicked_at = now
        self._retarget_after = now + (
            random.uniform(0.3, 0.8) if was_force
            else self._roll_retarget_cooldown())
        self._free.last_click_at = now
        self._free_expect_bar = True
        self._free_saw_bar = False
        self._free_bar_on_since = -1e9
        self._free_post_clear_watch = False
        self._free_force_retarget = False
        # After a hard-limit force click, do not lock TARGET BAR until the bar
        # has gone off once (prevents endless 40s re-holds on a sticky bar).
        self._free_need_bar_edge = bool(was_force)
        self._log.write(json.dumps({
            "t": round(now, 3), "action": "click",
            "x": pick.x, "y": pick.y, "targets": len(found),
            "area": pick.area, "mode": "free", "gate": gate,
            "raw": len(raw), "force": was_force,
            "dry_run": self.dry_run}) + "\n")
        return self._target_count, arm_s

    def _targeting_tick(self, win, now: float, show_aoi: bool,
                        hp: float = 1.0,
                        frame: np.ndarray | None = None
                        ) -> tuple[int, float | None]:
        """Detect zombies every tick; click when target bar is gone and HP ok.

        After each click, wait ``target_bar_arm_s`` for the target bar. When the
        bar clears (or after a miss), wait ``retarget_cooldown_s`` (default
        random 4.5–6s) and watch for a bar that appears on its own (multi-target
        auto-switch). Only click another NPC if the bar stays gone for that
        whole wait. Human mode wanders the mouse while waiting.
        """
        tcfg = self.cfg.get("targeting") or {}
        if not tcfg.get("enabled", False):
            self._target_count = 0
            self._last_pick = None
            return 0, None

        if not (tcfg.get("zombie_colors_bgr") or []):
            self._target_count = 0
            self._last_pick = None
            if show_aoi and not getattr(self, "_warned_no_colors", False):
                print("No zombie colours yet. Run:  python3 mark.py  (press z, click zombies)")
                self._warned_no_colors = True
            return 0, None

        # Full window grab so the cyan marker can be found after you walk.
        if frame is None:
            frame = self._grab.grab(win.region)
        region = aoi.locate_fence(win, self.cfg, frame, self._fence)

        if region is None:
            self._target_count = 0
            self._last_pick = None
            aoi_cfg = self.cfg.get("aoi") or {}
            needs_mark = len(aoi_cfg.get("polygon_offset") or []) < 3
            if not self._fence_lost:
                if needs_mark:
                    print("NEED MARK — run: python3 mark.py  (save while cyan marker blinks)")
                else:
                    print("MARKER LOST — keep the cyan blink on-screen (or re-run mark.py).")
                self._fence_lost = True
            if show_aoi:
                tile = np.full((140, 420, 3), (24, 22, 20), np.uint8)
                title = "NEED mark.py" if needs_mark else "MARKER LOST"
                cv2.putText(tile, title, (16, 48),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (80, 80, 255), 2)
                hint = ("save fence with cyan marker visible"
                        if needs_mark else
                        f"score {self._fence.last_score:.2f}  armed={self._fence.armed}")
                cv2.putText(tile, hint, (16, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                            (180, 180, 180), 1)
                cv2.imshow("RS3 AOI", tile)
            return 0, None

        if self._fence_lost:
            print(f"MARKER LOCKED  score={region.match_score:.2f}")
            self._fence_lost = False

        bx, by, bw, bh = region.bbox
        # Crop from the frame we already have (window-relative coords).
        lx, ly = bx - win.x, by - win.y
        crop = frame[ly:ly + bh, lx:lx + bw]
        found = targets.find_zombies(crop, region, origin=(bx, by), cfg=self.cfg)
        # Static method: same pixel detection, but only click blobs that have
        # not moved for targeting.static_still_s (default 2s).
        if self._attack_method() == "static":
            found = self._static.filter_targets(
                found, now, self.cfg.get("targeting"))
        self._target_count = len(found)

        arm_s = float(tcfg.get("target_bar_arm_s", 3.0))
        since_click = now - self._clicked_at
        waiting_for_bar = (
            self._miss_xy is not None and since_click < arm_s)

        # Top target-info bar: probe match, or charcoal/tan frame fallback.
        if self._use_probes:
            bar_on = probes.color_matches(
                frame,
                (self.cfg.get("probes") or {}).get("target_bar") or {},
                probes.tolerance(self.cfg.get("probes")),
            ) if probes.has_probe(self.cfg.get("probes"), "target_bar") else False
        else:
            bar_on = combat.target_bar_visible(frame, self.cfg.get("target_bar"))

        # Bar just cleared → wait random cooldown and watch for an auto-new
        # target bar before clicking elsewhere.
        if self._bar_was_on and not bar_on:
            self._retarget_after = now + self._roll_retarget_cooldown()
        self._bar_was_on = bar_on

        retarget_pct = float(self.cfg.get(
            "retarget_above_hp_pct",
            self.cfg.get("eat_below_pct", 50)))
        hp_ok = (hp >= 0.5) if self._use_probes else hp > (retarget_pct / 100.0)
        click_in: float | None = None
        pool = found
        cooling = now < self._retarget_after

        if bar_on:
            # Engaged (including auto-acquired next target) — do not click away.
            self._miss_xy = None
            self._clicked_at = -1e9
            allow_click = False
            gate = "TARGET BAR"
        elif waiting_for_bar:
            # Just clicked — give the bar time to appear before calling a miss.
            allow_click = False
            gate = "WAIT BAR"
            click_in = max(0.0, arm_s - since_click)
        elif cooling:
            # Post-clear / post-click watch — bar may still appear on its own.
            allow_click = False
            gate = "COOLDOWN"
            click_in = max(0.0, self._retarget_after - now)
        elif not hp_ok:
            allow_click = False
            gate = "WAIT HP"
        else:
            # Bar still gone after the watch window → click next target.
            allow_click = True
            gate = "READY"
            if self._miss_xy is not None and found:
                mx, my = self._miss_xy
                if self._click_abort_reason == "cyan_trap":
                    skip_r = int(tcfg.get("cyan_avoid_size_px", 100))
                else:
                    skip_r = int(tcfg.get("miss_skip_radius_px", 55))
                skip_r2 = skip_r * skip_r
                others = [t for t in found
                          if (t.x - mx) ** 2 + (t.y - my) ** 2 > skip_r2]
                if others:
                    pool = others
                    gate = ("CYAN SKIP" if self._click_abort_reason == "cyan_trap"
                            else "RETRY")
                elif (self._click_abort_reason == "cyan_trap"
                      and len(found) >= 1):
                    pool = [max(
                        found,
                        key=lambda t: (t.x - mx) ** 2 + (t.y - my) ** 2)]
                    gate = "CYAN SKIP"

        if self._eat_suspended:
            allow_click = False
            gate = "EAT STOP"

        if tcfg.get("click_nearest", True):
            pick = targets.nearest(pool, region.anchor_x, region.anchor_y)
            pick_mode = "nearest"
        else:
            pick = targets.largest(pool)
            pick_mode = "densest"
        self._last_pick = (pick.x, pick.y) if pick else None

        status = f"MARKER {region.match_score:.2f}  {gate}"
        if gate in ("WAIT BAR", "COOLDOWN") and click_in is not None:
            status = f"MARKER {region.match_score:.2f}  {gate} {click_in:.1f}s"
        if gate == "EAT STOP":
            status = "SAFE STOP eat failed — loot/bury/combat paused"
        self._last_action = status

        # Human: wander inside the AOI while waiting on bar / cooldown.
        # Requires RuneScape frontmost (safety). Works in dry-run too (mouse only).
        if self._is_human() and gate in ("TARGET BAR", "WAIT BAR", "COOLDOWN"):
            self._human.cfg = dict(self.cfg.get("human") or {})
            x0, y0, w0, h0 = region.bbox
            self._human.wait_wander(
                (x0, y0, x0 + w0, y0 + h0),
                mode=self.cfg.get("key_mode", "hid"),
                pid=win.pid,
            )

        if show_aoi:
            # Preview the full search box (mark.py `v`) with the green fence
            # drawn inside — not just the tight fence bbox crop (which looks
            # tiny even when the real AOI is large).
            mcfg_prev = aoi._marker_cfg(self.cfg.get("aoi") or {})
            search = aoi.resolve_search_rect(mcfg_prev, win.width, win.height)
            if search is not None:
                sx0, sy0, sx1, sy1 = search
                prev_ox, prev_oy = win.x + sx0, win.y + sy0
                prev_crop = frame[sy0:sy1, sx0:sx1]
            else:
                prev_ox, prev_oy = bx, by
                prev_crop = crop
            if prev_crop.size > 0:
                vis = aoi.draw_overlay(
                    prev_crop, region,
                    blobs=[(t.x, t.y) for t in found],
                    pick=self._last_pick,
                    origin=(prev_ox, prev_oy),
                    status=status,
                    marker_xy=(win.x + self._fence.last_mx,
                               win.y + self._fence.last_my))
                # Search-box outline so you see the full marked playfield.
                if search is not None:
                    cv2.rectangle(vis, (0, 0),
                                  (vis.shape[1] - 1, vis.shape[0] - 1),
                                  (255, 180, 0), 2)
                max_w = 900
                if vis.shape[1] > max_w:
                    scale = max_w / vis.shape[1]
                    vis = cv2.resize(vis, None, fx=scale, fy=scale,
                                    interpolation=cv2.INTER_AREA)
                cv2.imshow("RS3 AOI", vis)

        if pick is None or not allow_click or not self._attack_enabled():
            return self._target_count, click_in

        if not aoi.is_valid_click(pick.x, pick.y, region):
            return self._target_count, click_in

        why = f"zombie {pick_mode} of {len(found)} (area={pick.area})"
        clicked = self._click(pick.x, pick.y, win, why)
        if not clicked:
            self._miss_xy = (pick.x, pick.y)
            reason = self._click_abort_reason or "pixel_gone"
            self._log.write(json.dumps({
                "t": round(now, 3), "action": "click_abort",
                "x": pick.x, "y": pick.y, "reason": reason,
                "mode": pick_mode, "dry_run": self.dry_run}) + "\n")
            return self._target_count, click_in

        self._miss_xy = (pick.x, pick.y)
        self._clicked_at = now
        self._retarget_after = now + self._roll_retarget_cooldown()
        self._log.write(json.dumps({
            "t": round(now, 3), "action": "click",
            "x": pick.x, "y": pick.y, "targets": len(found),
            "area": pick.area, "mode": pick_mode, "gate": gate,
            "lock": round(region.match_score, 3),
            "dry_run": self.dry_run}) + "\n")
        return self._target_count, arm_s

    # ---------- the loop ----------

    def run(self) -> int:
        period = 1.0 / self.cfg.get("loop_hz", 20)
        show = self.cfg.get("overlay", True)
        show_aoi = self.cfg.get("aoi_overlay", True)
        threshold = self.cfg["eat_below_pct"] / 100.0
        ability_keys = self.cfg.get("ability_keys") or []
        targeting_on = (self.cfg.get("targeting") or {}).get("enabled", False)

        print("Bot running.  F12 = pause/resume,  Esc x3 = stop,  Ctrl-C = stop")
        print("Overlay: click Attack/Method/Eat/Loot/Bury/Inv/Still/Food quit/Cyan/Rotate +/−, "
              "or b / m / e / o / i / u / s / f / c / r  (q = quit)")
        print("Kills = target-bar clear count (session); shown on overlay as kills/hr.")
        print("Food quit off = SAFE STOP pause only; on = print kills and exit after "
              f"eat fail ×{int(self.cfg.get('eat_max_failures', 2))}.")
        print("Cyan avoid off by default (dungeon cyan tiles false-trigger). "
              "Turn on near real traps; adjust Cyan box if needed.")
        print("Rotate off by default — on = Right Arrow hold (Rot hold) every Rot gap.")
        self._session_started_at = time.time()
        self._kills = 0
        self._session_summary_printed = False
        self._next_rotate_at = self._session_started_at + self._roll_targeting_range(
            "rotate_interval_s", default=(3.0, 10.0))
        if self.dry_run:
            print("DRY RUN: reading everything, pressing/clicking nothing.")
        elif not ability_keys:
            print("No ability keys configured, so only food (and targeting) run.")
        if targeting_on:
            arm = (self.cfg.get("targeting") or {}).get("target_bar_arm_s", 3.0)
            cool = (self.cfg.get("targeting") or {}).get(
                "retarget_cooldown_s", [4.5, 6.0])
            pick = "nearest-to-center" if (self.cfg.get("targeting") or {}).get(
                "click_nearest", True) else "densest"
            print(f"Targeting ON: {pick} still blob; wait {arm}s for bar after click; "
                  f"{cool}s watch after bar clears (skip click if bar returns); "
                  "miss → next target; pre-click pixel verify; "
                  "human wanders while waiting.")
        print(f"Attack: {self._attack_style()}  |  Method: {self._attack_method()}"
              f"  |  Eat: {self._eat_mode()}"
              f"  |  Loot: {self._loot_mode()}"
              f"  |  Bury: {self._bury_mode()}"
              f"  |  Inv box: {'on' if self._loot_use_inv() else 'off'}"
              f"  |  Free still: {'on' if self._free_require_still() else 'off'}"
              f"  |  Food quit: {'on' if self._quit_on_eat_fail() else 'off'}"
              f"  |  Cyan avoid: {'on' if self._cyan_avoid_enabled() else 'off'}"
              f"  |  Rotate: {'on' if self._rotate_screen() else 'off'}")
        print("Eat + loot + bury keys always use human timing (pre/hold/post delays).")
        print("Loot target = Space after target bar clears; always = Space every 2–5s random "
              "(Inv box off = no inventory/button probes).")
        print("Bury = ] (or bury.bury_key); after_combat / always same as loot.")
        if self._attack_method() == "static":
            still = float((self.cfg.get("targeting") or {}).get(
                "static_still_s", static_target.DEFAULT_STILL_S))
            print(f"Static method: click pixel blobs only after ~{still:.1f}s stillness.")
        elif self._attack_method() == "free":
            still = float((self.cfg.get("targeting") or {}).get(
                "static_still_s", static_target.DEFAULT_STILL_S))
            if self._free_require_still():
                print(f"Free method: visible colours + still ~{still:.1f}s + target-bar "
                      "(no cyan marker / fence).")
            else:
                print("Free method: colour-match on playfield + target-bar "
                      "(no morph/density; still wait off; pre-click verify on).")
        if self._attack_style() == "human":
            print("Human mouse: curved clicks + wander while TARGET BAR / COOLDOWN.")
            print("  → Click the RuneScape window so it is frontmost, or movement is paused.")
        if self._attack_style() == "off":
            print("Attack off — targeting clicks and ability keys disabled.")

        click_in: float | None = None

        if show:
            cv2.namedWindow("RS3 bot", cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback("RS3 bot", self._on_overlay_mouse)
            self._overlay_ready = True
            self._overlay_dirty = False
            self._overlay_cache = {}

        while not self.stop:
            tick = time.time()
            win = window.find_game()
            target_count = 0
            under_attack = False

            if win is None:
                status, colour = "RuneScape is not open", (120, 120, 200)
                hp = adren = 0.0
                fighting, reason, score = False, "game closed", 0.0
                self._rotate_release_now(None)
            elif not window.is_frontmost():
                status, colour = "PAUSED - game not in front", (0, 190, 255)
                frame = self._grab.grab(win.region)
                hp, adren, ad_img, tx_img, fighting, under_attack, reason, score = (
                    self.read(win, frame))
                if not self._use_probes:
                    fighting = self._combat.update(tx_img, ad_img, hp, tick)
                    reason, score = self._combat.last_reason, self._combat.last_score
                self._rotate_release_now(win)
            elif self.paused:
                status, colour = "PAUSED (F12 to resume)", (0, 190, 255)
                frame = self._grab.grab(win.region)
                hp, adren, ad_img, tx_img, fighting, under_attack, reason, score = (
                    self.read(win, frame))
                if not self._use_probes:
                    fighting = self._combat.update(tx_img, ad_img, hp, tick)
                    reason, score = self._combat.last_reason, self._combat.last_score
                self._maybe_rotate(win, tick)  # finish any in-flight key-up
            else:
                status, colour = "RUNNING", (90, 235, 90)
                frame = self._grab.grab(win.region)
                hp, adren, ad_img, tx_img, fighting, under_attack, reason, score = (
                    self.read(win, frame))
                if not self._use_probes:
                    fighting = self._combat.update(tx_img, ad_img, hp, tick)
                    reason, score = self._combat.last_reason, self._combat.last_score
                    under_attack = combat.target_bar_visible(
                        frame, self.cfg.get("target_bar"))
                pressed = self.decide(hp, fighting, tick, win,
                                      under_attack=under_attack)
                if pressed:
                    self._log.write(json.dumps({
                        "t": round(tick, 3), "hp": round(hp, 4),
                        "adren": round(adren, 4), "fighting": fighting,
                        "key": pressed, "dry_run": self.dry_run}) + "\n")
                self._note_target_bar(under_attack, tick)
                loot_key = self._maybe_loot(frame, win, tick)
                if loot_key:
                    self._log.write(json.dumps({
                        "t": round(tick, 3), "action": "loot",
                        "key": loot_key, "dry_run": self.dry_run}) + "\n")
                bury_key = self._maybe_bury(win, tick)
                if bury_key:
                    self._log.write(json.dumps({
                        "t": round(tick, 3), "action": "bury",
                        "key": bury_key, "dry_run": self.dry_run}) + "\n")
                rot = self._maybe_rotate(win, tick)
                if rot:
                    self._log.write(json.dumps({
                        "t": round(tick, 3), "action": rot,
                        "key": self._rotate_key_name(),
                        "dry_run": self.dry_run}) + "\n")
                self._skip_support_keys_once = False
                target_count, click_in = (
                    self._free_pixel_tick(
                        win, tick, show_aoi, hp=hp, frame=frame)
                    if self._attack_method() == "free"
                    else self._targeting_tick(
                        win, tick, show_aoi, hp=hp, frame=frame))
                if self._is_human() and win is not None and not under_attack:
                    # Idle fidget only when not mid-fight; fight waits use wait_wander.
                    self._human.maybe_idle_fidget(
                        mode=self.cfg.get("key_mode", "hid"), pid=win.pid)

            if show:
                now_ui = time.time()
                kw = self._overlay_render_kwargs(
                    hp=hp, adren=adren, fighting=fighting, reason=reason,
                    score=score, status=status, status_colour=colour,
                    eat_threshold=threshold, ability_keys=ability_keys,
                    target_count=target_count if win else self._target_count,
                    click_in_s=click_in, probe_mode=self._use_probes,
                    under_attack=under_attack, now=now_ui,
                )
                self._overlay_cache = kw
                cv2.imshow("RS3 bot", overlay.render(**kw))
                self._overlay_dirty = False
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    self.stop = True
                elif key == ord("b"):
                    self._toggle_attack_style()
                    self._overlay_dirty = True
                elif key == ord("m"):
                    self._toggle_attack_method()
                    self._overlay_dirty = True
                elif key == ord("e"):
                    self._toggle_eat_mode()
                    self._overlay_dirty = True
                elif key == ord("o"):
                    self._toggle_loot_mode()
                    self._overlay_dirty = True
                elif key == ord("i"):
                    self._toggle_loot_use_inv()
                    self._overlay_dirty = True
                elif key == ord("u"):
                    self._toggle_bury_mode()
                    self._overlay_dirty = True
                elif key == ord("s"):
                    self._toggle_free_require_still()
                    self._overlay_dirty = True
                elif key == ord("f"):
                    self._toggle_quit_on_eat_fail()
                    self._overlay_dirty = True
                elif key == ord("c"):
                    self._toggle_cyan_avoid()
                    self._overlay_dirty = True
                elif key == ord("r"):
                    self._toggle_rotate_screen()
                    self._overlay_dirty = True
                if self._overlay_dirty:
                    self._redraw_overlay_now()

            slept = time.time() - tick
            if slept < period:
                if show:
                    self._pump_overlay_idle(period - slept)
                else:
                    time.sleep(period - slept)

        self._rotate_release_now(window.find_game())
        if show or show_aoi:
            cv2.destroyAllWindows()
        self._log.close()
        if self._session_summary_printed:
            print("Stopped.")
        else:
            self._print_session_kills(prefix="Stopped.  Session kills")
        return 0
