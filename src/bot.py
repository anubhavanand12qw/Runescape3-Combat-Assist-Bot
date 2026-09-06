"""The brain: one loop that watches probes/bars, eats safely, and clicks zombies.

    is the game in front?  no -> do nothing
    health probe changed?  yes -> press food (stops after 2 failed eats)
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

from . import aoi, bars, capture, combat, human, keys, overlay, probes, targets, window


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
    _use_probes: bool = field(default=False, init=False)

    # Eat fail-safe: stop pressing food after N failed attempts until HP recovers.
    _eat_failures: int = field(default=0, init=False)
    _eat_suspended: bool = field(default=False, init=False)
    _hp_at_eat: float | None = field(default=None, init=False)
    _awaiting_eat_result: bool = field(default=False, init=False)

    _target_count: int = field(default=0, init=False)
    _last_pick: tuple[int, int] | None = field(default=None, init=False)
    _fence: aoi.MarkerTracker = field(default_factory=aoi.MarkerTracker, init=False)
    _fence_lost: bool = field(default=False, init=False)

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
        self.cfg["behavior"].setdefault("eat_mode", "low_hp")
        self._human = human.HumanSession(cfg=dict(self.cfg.get("human") or {}))

        Path("logs").mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._log = open(f"logs/session-{stamp}.jsonl", "a", buffering=1)

    def _behavior(self) -> dict:
        return self.cfg.setdefault("behavior", {})

    def _attack_style(self) -> str:
        return str(self._behavior().get("attack_style", "bot")).lower()

    def _eat_mode(self) -> str:
        return str(self._behavior().get("eat_mode", "low_hp")).lower()

    def _is_human(self) -> bool:
        return self._attack_style() == "human"

    def _toggle_attack_style(self, force: str | None = None) -> None:
        b = self._behavior()
        if force in ("bot", "human"):
            b["attack_style"] = force
        else:
            b["attack_style"] = "human" if self._is_human() else "bot"
        style = b["attack_style"]
        self._last_action = f"attack style → {style}"
        print(f"Attack style: {style}")
        if style == "human":
            print("  Human: curved clicks + mouse wander while waiting.")
            print("  Keep RuneScape the front window — otherwise it pauses and won't move.")

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

    def _on_overlay_mouse(self, event, x, y, flags, param) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        hit = overlay.hit_test(x, y)
        if hit == "attack_bot":
            self._toggle_attack_style("bot")
        elif hit == "attack_human":
            self._toggle_attack_style("human")
        elif hit == "eat_low_hp":
            self._toggle_eat_mode("low_hp")
        elif hit == "eat_in_combat":
            self._toggle_eat_mode("in_combat")

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
            note = self._human.maybe_break()
            if note:
                self._last_action = note
            human.press_human(key, self.cfg.get("human"), mode=mode, pid=win.pid)
        else:
            keys.press(key, mode=mode, pid=win.pid)
        self._last_action = f"pressed {key} ({why}) at {datetime.now():%H:%M:%S}"

    def _click(self, x: int, y: int, win, why: str) -> None:
        if self.dry_run:
            self._last_action = f"[dry run] would click ({x},{y}) ({why})"
            return
        mode = self.cfg.get("key_mode", "hid")
        if self._is_human():
            # Never break/fidget before a target click — delay = NPC walked away.
            human.click_human(x, y, self.cfg.get("human"), mode=mode, pid=win.pid)
        else:
            keys.click(x, y, mode=mode, pid=win.pid)
        self._last_action = f"clicked ({x},{y}) ({why}) at {datetime.now():%H:%M:%S}"

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
            self._last_action = (
                f"eat suspended after {self._eat_failures} failed attempts")

    def _maybe_resume_eat(self, hp: float, threshold: float) -> None:
        ok = hp >= 0.5 if self._use_probes else hp >= threshold
        if self._eat_suspended and ok:
            self._eat_suspended = False
            self._eat_failures = 0
            self._last_action = "eat resumed (health back above threshold)"

    def decide(self, hp: float, fighting: bool, now: float, win,
               under_attack: bool = False) -> str | None:
        food_key = self.cfg.get("food_key")
        threshold = self.cfg["eat_below_pct"] / 100.0
        self._maybe_resume_eat(hp, threshold)

        # Only judge the previous eat once the cooldown has elapsed.
        if (self._awaiting_eat_result
                and (now - self._last_eat) >= self.cfg["eat_cooldown_s"]):
            self._update_eat_result(hp)

        if self._use_probes:
            need_eat = food_key and not self._eat_suspended and hp < 0.5
        else:
            need_eat = food_key and not self._eat_suspended and hp < threshold
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
            self._press(food_key, win, why)
            return food_key

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

    def _targeting_tick(self, win, now: float, show_aoi: bool,
                        hp: float = 1.0,
                        frame: np.ndarray | None = None
                        ) -> tuple[int, float | None]:
        """Detect zombies every tick; click when target bar is gone and HP ok.

        After each click, wait ``target_bar_arm_s`` (default 3s) for the top
        target-info bar. If it never appears, retry the next densest blob.
        After a kill (bar clears), wait ``retarget_cooldown_s`` (default 3s)
        before the next click. Human mode wanders the mouse while waiting.
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
        self._target_count = len(found)

        arm_s = float(tcfg.get("target_bar_arm_s", 3.0))
        cooldown_s = float(tcfg.get("retarget_cooldown_s", 3.0))
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

        # Bar just cleared → start retarget cooldown before next click.
        if self._bar_was_on and not bar_on:
            self._retarget_after = now + cooldown_s
        self._bar_was_on = bar_on

        retarget_pct = float(self.cfg.get(
            "retarget_above_hp_pct",
            self.cfg.get("eat_below_pct", 50)))
        hp_ok = (hp >= 0.5) if self._use_probes else hp > (retarget_pct / 100.0)
        click_in: float | None = None
        pool = found
        cooling = now < self._retarget_after

        if bar_on:
            # Engaged — hold fire until the bar clears.
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
            allow_click = False
            gate = "COOLDOWN"
            click_in = max(0.0, self._retarget_after - now)
        elif not hp_ok:
            allow_click = False
            gate = "WAIT HP"
        else:
            # Bar gone + cooldown done, or arm window expired with no bar → click.
            allow_click = True
            gate = "READY"
            if self._miss_xy is not None and found:
                mx, my = self._miss_xy
                skip_r = int(tcfg.get("miss_skip_radius_px", 55))
                skip_r2 = skip_r * skip_r
                others = [t for t in found
                          if (t.x - mx) ** 2 + (t.y - my) ** 2 > skip_r2]
                if others:
                    pool = others
                    gate = "RETRY"

        if tcfg.get("click_nearest", False):
            pick = targets.nearest(pool, region.anchor_x, region.anchor_y)
            pick_mode = "nearest"
        else:
            pick = targets.largest(pool)
            pick_mode = "densest"
        self._last_pick = (pick.x, pick.y) if pick else None

        status = f"MARKER {region.match_score:.2f}  {gate}"
        if gate in ("WAIT BAR", "COOLDOWN") and click_in is not None:
            status = f"MARKER {region.match_score:.2f}  {gate} {click_in:.1f}s"

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
            vis = aoi.draw_overlay(
                crop, region,
                blobs=[(t.x, t.y) for t in found],
                pick=self._last_pick,
                origin=(bx, by),
                status=status,
                marker_xy=(win.x + self._fence.last_mx, win.y + self._fence.last_my))
            max_w = 900
            if vis.shape[1] > max_w:
                scale = max_w / vis.shape[1]
                vis = cv2.resize(vis, None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_AREA)
            cv2.imshow("RS3 AOI", vis)

        if pick is None or not allow_click:
            return self._target_count, click_in

        if not aoi.is_valid_click(pick.x, pick.y, region):
            return self._target_count, click_in

        self._miss_xy = (pick.x, pick.y)
        self._clicked_at = now
        why = f"zombie {pick_mode} of {len(found)} (area={pick.area})"
        self._click(pick.x, pick.y, win, why)
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
        print("Overlay: click Attack/Eat boxes, or press b / e  (q = quit)")
        if self.dry_run:
            print("DRY RUN: reading everything, pressing/clicking nothing.")
        elif not ability_keys:
            print("No ability keys configured, so only food (and targeting) run.")
        if targeting_on:
            arm = (self.cfg.get("targeting") or {}).get("target_bar_arm_s", 3.0)
            cool = (self.cfg.get("targeting") or {}).get("retarget_cooldown_s", 3.0)
            print(f"Targeting ON: densest blob; wait {arm}s for bar after click; "
                  f"{cool}s cooldown after kill; miss → next target; "
                  "human wanders while waiting.")
        print(f"Attack style: {self._attack_style()}  |  Eat mode: {self._eat_mode()}")
        if self._attack_style() == "human":
            print("Human mouse: curved clicks + wander while TARGET BAR / COOLDOWN.")
            print("  → Click the RuneScape window so it is frontmost, or movement is paused.")

        click_in: float | None = None

        if show:
            cv2.namedWindow("RS3 bot")
            cv2.setMouseCallback("RS3 bot", self._on_overlay_mouse)
            self._overlay_ready = True

        while not self.stop:
            tick = time.time()
            win = window.find_game()
            target_count = 0
            under_attack = False

            if win is None:
                status, colour = "RuneScape is not open", (120, 120, 200)
                hp = adren = 0.0
                fighting, reason, score = False, "game closed", 0.0
            elif not window.is_frontmost():
                status, colour = "PAUSED - game not in front", (0, 190, 255)
                frame = self._grab.grab(win.region)
                hp, adren, ad_img, tx_img, fighting, under_attack, reason, score = (
                    self.read(win, frame))
                if not self._use_probes:
                    fighting = self._combat.update(tx_img, ad_img, hp, tick)
                    reason, score = self._combat.last_reason, self._combat.last_score
            elif self.paused:
                status, colour = "PAUSED (F12 to resume)", (0, 190, 255)
                frame = self._grab.grab(win.region)
                hp, adren, ad_img, tx_img, fighting, under_attack, reason, score = (
                    self.read(win, frame))
                if not self._use_probes:
                    fighting = self._combat.update(tx_img, ad_img, hp, tick)
                    reason, score = self._combat.last_reason, self._combat.last_score
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
                target_count, click_in = self._targeting_tick(
                    win, tick, show_aoi, hp=hp, frame=frame)
                if self._is_human() and win is not None and not under_attack:
                    # Idle fidget only when not mid-fight; fight waits use wait_wander.
                    self._human.maybe_idle_fidget(
                        mode=self.cfg.get("key_mode", "hid"), pid=win.pid)

            if show:
                cv2.imshow("RS3 bot", overlay.render(
                    hp=hp, adren=adren, fighting=fighting, reason=reason,
                    score=score, status=status, status_colour=colour,
                    last_action=self._last_action, eat_threshold=threshold,
                    dry_run=self.dry_run, ability_keys=ability_keys,
                    eat_suspended=self._eat_suspended,
                    eat_failures=self._eat_failures,
                    target_count=target_count if win else self._target_count,
                    click_in_s=click_in,
                    attack_style=self._attack_style(),
                    eat_mode=self._eat_mode(),
                    probe_mode=self._use_probes,
                    under_attack=under_attack))
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    self.stop = True
                elif key == ord("b"):
                    self._toggle_attack_style()
                elif key == ord("e"):
                    self._toggle_eat_mode()

            slept = time.time() - tick
            if slept < period:
                time.sleep(period - slept)

        if show or show_aoi:
            cv2.destroyAllWindows()
        self._log.close()
        print("Stopped.")
        return 0
