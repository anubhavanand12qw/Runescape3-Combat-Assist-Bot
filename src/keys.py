"""Send key presses to macOS so RuneScape sees them.

We use Quartz CGEvent, which is the standard way to synthesise keyboard input on
macOS. Two delivery modes exist because games are picky:

  "hid"  -> post to the system-wide HID tap. This looks the most like a real
            keyboard and is what we try first.
  "pid"  -> post straight into the rs2client process. Fallback for when a game
            ignores the HID tap.

Requires: System Settings -> Privacy & Security -> Accessibility -> your Terminal.
"""

from __future__ import annotations

import random
import time

import ApplicationServices
import Quartz

# macOS ANSI virtual keycodes for the number row. These are the physical keys,
# not characters, which is exactly what a game's keybinds listen for.
KEYCODES: dict[str, int] = {
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23,
    "6": 22, "7": 26, "8": 28, "9": 25, "0": 29,
    "-": 27, "=": 24,
}

# How long a key stays held down, in seconds. Randomised inside this range so the
# rhythm is not perfectly robotic.
HOLD_RANGE = (0.030, 0.075)


class KeySenderError(RuntimeError):
    pass


def accessibility_granted(prompt: bool = False) -> bool:
    """True if this process is allowed to synthesise input.

    With prompt=True macOS shows its "open Accessibility settings" dialog once.
    """
    if prompt:
        opts = {ApplicationServices.kAXTrustedCheckOptionPrompt: True}
        return bool(ApplicationServices.AXIsProcessTrustedWithOptions(opts))
    return bool(ApplicationServices.AXIsProcessTrusted())


def _post(event, mode: str, pid: int | None) -> None:
    if mode == "pid":
        if pid is None:
            raise KeySenderError('mode="pid" needs the rs2client pid')
        Quartz.CGEventPostToPid(pid, event)
    else:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def press(key: str, mode: str = "hid", pid: int | None = None,
          hold: float | None = None) -> None:
    """Tap a single key. `key` is one of the characters in KEYCODES."""
    try:
        code = KEYCODES[key]
    except KeyError:
        raise KeySenderError(
            f"key {key!r} is not mapped; known keys: {''.join(KEYCODES)}"
        ) from None

    source = Quartz.CGEventSourceCreate(
        Quartz.kCGEventSourceStateHIDSystemState
    )
    down = Quartz.CGEventCreateKeyboardEvent(source, code, True)
    up = Quartz.CGEventCreateKeyboardEvent(source, code, False)

    _post(down, mode, pid)
    time.sleep(hold if hold is not None else random.uniform(*HOLD_RANGE))
    _post(up, mode, pid)


CLICK_HOLD_RANGE = (0.040, 0.090)


def click(x: int, y: int, mode: str = "hid", pid: int | None = None,
          hold: float | None = None) -> None:
    """Left-click at absolute screen coordinates (macOS points).

    Moves the cursor, presses, then releases. Same Accessibility permission as
    key presses. Prefer mode=\"hid\" unless the game ignores it.
    """
    source = Quartz.CGEventSourceCreate(
        Quartz.kCGEventSourceStateHIDSystemState
    )
    # Move first so the game sees the cursor over the target before the click.
    move = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventMouseMoved, (x, y),
        Quartz.kCGMouseButtonLeft)
    down = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventLeftMouseDown, (x, y),
        Quartz.kCGMouseButtonLeft)
    up = Quartz.CGEventCreateMouseEvent(
        source, Quartz.kCGEventLeftMouseUp, (x, y),
        Quartz.kCGMouseButtonLeft)

    _post(move, mode, pid)
    time.sleep(random.uniform(0.010, 0.030))
    _post(down, mode, pid)
    time.sleep(hold if hold is not None else random.uniform(*CLICK_HOLD_RANGE))
    _post(up, mode, pid)
