#!/usr/bin/env python3
"""STEP 1: prove that key presses actually reach RuneScape.

Everything else in this bot is built on top of this working, so test it first.

    python3 keytest.py            # taps "1" using the normal method
    python3 keytest.py 2          # tap a different key
    python3 keytest.py 1 --pid    # use the fallback method (aim at the game)

Watch the game. If the ability on that key fires (or the chat says "Ability not
ready yet"), it worked. If absolutely nothing happens, try --pid.
"""

from __future__ import annotations

import sys
import time

from src import keys, window


def countdown(seconds: int = 3) -> None:
    print(f"\nClick on the RuneScape window now. Sending in {seconds}...")
    for i in range(seconds, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    key = args[0] if args else "1"
    mode = "pid" if "--pid" in sys.argv else "hid"

    if key not in keys.KEYCODES:
        print(f"'{key}' is not a key I can send. Try one of: {' '.join(keys.KEYCODES)}")
        return 1

    if not keys.accessibility_granted():
        print("\nmacOS is blocking key presses from this program.")
        print("Fix it once:")
        print("  System Settings -> Privacy & Security -> Accessibility")
        print("  turn ON the app you are running this from (Terminal, iTerm, VS Code...)")
        print("  then FULLY QUIT that app and open it again - macOS caches the old answer.")
        keys.accessibility_granted(prompt=True)   # pops the system dialog once
        return 1
    print("Accessibility permission: granted")

    win = window.find_game()
    if win is None:
        print("RuneScape is not open. Start the game and try again.")
        return 1
    print(f"Found RuneScape (pid {win.pid}) at ({win.x}, {win.y}) {win.width}x{win.height}")

    countdown()

    if not window.is_frontmost():
        print("RuneScape is not the front window - the key would go to another app.")
        print("Click the game window and run this again.")
        return 1

    keys.press(key, mode=mode, pid=win.pid if mode == "pid" else None)
    print(f"\nSent '{key}' using the {mode!r} method.")
    print("Did the game react?")
    print("  YES -> you are done. Run:  python3 calibrate.py")
    if mode == "hid":
        print(f"  NO  -> try the fallback:  python3 keytest.py {key} --pid")
    else:
        print("  NO  -> re-check Accessibility, and fully restart your terminal app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
