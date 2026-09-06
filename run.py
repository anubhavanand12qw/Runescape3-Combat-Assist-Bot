#!/usr/bin/env python3
"""Start the RS3 assistant.

    python3 run.py --dry-run    watch everything, press/click nothing  <- start here
    python3 run.py              for real
    python3 run.py --no-overlay hide the status readout
    python3 run.py --no-aoi     hide the AOI targeting overlay
    python3 run.py --skip-autocal  do not re-measure bars on start

While it runs:
    F12          pause / resume
    Esc Esc Esc  stop (three taps, quickly)
    q            stop, when a readout window has focus
    Ctrl-C       stop
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from src import autocal, config_util, keys, probes, window
from src.bot import Bot

CONFIG_PATH = Path("config.json")
ESC_TAPS_TO_STOP = 3
ESC_WINDOW_S = 1.5


def load_config() -> dict | None:
    cfg = config_util.load_merged()
    if cfg is None:
        print("No config found. Run:  python3 mark.py")
        return None
    if not CONFIG_PATH.exists():
        print("No config.json yet, using config.default.json.")
        print("Set fence + probes with:  python3 mark.py")
    return cfg


def attach_hotkeys(bot: Bot) -> None:
    """F12 pauses, three quick Escapes stop. Best effort - never fatal."""
    try:
        from pynput import keyboard as kb
    except Exception as exc:
        print(f"Hotkeys unavailable ({exc}). Use Ctrl-C or 'q' in the window.")
        return

    taps: list[float] = []

    def on_press(k):
        nonlocal taps
        if k == kb.Key.f12:
            bot.paused = not bot.paused
            print("PAUSED" if bot.paused else "RESUMED")
        elif k == kb.Key.esc:
            now = time.time()
            taps = [t for t in taps if now - t < ESC_WINDOW_S] + [now]
            if len(taps) >= ESC_TAPS_TO_STOP:
                print("Stop requested (Esc x3).")
                bot.stop = True

    try:
        kb.Listener(on_press=on_press, daemon=True).start()
    except Exception as exc:
        print(f"Hotkeys could not start ({exc}). Use Ctrl-C or 'q' in the window.")


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if window.find_game() is None:
        print("RuneScape is not open. Start the game and try again.")
        print("Tip: fullscreen on another Space is OK — leave the client running.")
        print("     If this still fails, grant Screen Recording to your terminal app")
        print("     (System Settings → Privacy & Security → Screen Recording), then restart it.")
        return 1

    if "--skip-autocal" not in sys.argv:
        # Probe mode does not need bar-box auto-measure.
        early = config_util.load_merged() or {}
        if probes.probes_ready(early.get("probes")):
            print("Probes configured — skipping bar auto-cal.")
        else:
            err = autocal.measure_and_save(require_zero_adren=False)
            if err:
                print(f"Auto-config skipped: {err}")
    
    cfg = load_config()
    if cfg is None:
        return 1
    if "--no-overlay" in sys.argv:
        cfg["overlay"] = False
    if "--no-aoi" in sys.argv:
        cfg["aoi_overlay"] = False

    if not dry_run and not keys.accessibility_granted():
        print("\nmacOS is blocking key presses / clicks from this program.")
        print("  System Settings -> Privacy & Security -> Accessibility")
        print("  turn ON your terminal app, then fully quit and reopen it.")
        print("\nYou can still watch without pressing anything:")
        print("  python3 run.py --dry-run")
        return 1

    bot = Bot(cfg=cfg, dry_run=dry_run)
    attach_hotkeys(bot)
    try:
        return bot.run()
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
