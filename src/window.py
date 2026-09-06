"""Find the RuneScape window and tell whether it is the window in front.

Everything is in macOS "points", the same coordinate space mss grabs in, so the
numbers here can be handed straight to a screen grab with no scaling.

Fullscreen note: when RS is on its own Space, ``OnScreenOnly`` from another
Space (e.g. Terminal) often misses it — we fall back to scanning all windows
and pick the largest ``rs2client`` surface.
"""

from __future__ import annotations

from dataclasses import dataclass

import Quartz

# Process / window names seen across windowed, maximized, and fullscreen.
OWNER_NAMES = frozenset({"rs2client", "RuneScape"})
TITLE_HINTS = ("RuneScape", "RuneScape 3", "RS3")
MIN_W, MIN_H = 400, 300


@dataclass(frozen=True)
class GameWindow:
    x: int
    y: int
    width: int
    height: int
    pid: int
    title: str
    window_id: int = 0

    @property
    def region(self) -> dict[str, int]:
        """As an mss grab region (points). Prefer Grabber for actual pixels."""
        return {"left": self.x, "top": self.y,
                "width": self.width, "height": self.height}

    def frac_to_region(self, frac: tuple[float, float, float, float]) -> dict[str, int]:
        """Turn (x, y, w, h) fractions of this window into an mss grab region.

        Storing boxes as fractions is what lets the setup survive the game window
        being moved or resized.
        """
        fx, fy, fw, fh = frac
        return {
            "left": self.x + int(round(fx * self.width)),
            "top": self.y + int(round(fy * self.height)),
            "width": max(1, int(round(fw * self.width))),
            "height": max(1, int(round(fh * self.height))),
        }


def _is_game_owner_or_title(owner: str, title: str) -> bool:
    if owner in OWNER_NAMES:
        return True
    return any(h in title for h in TITLE_HINTS)


def _candidates(windows) -> list[GameWindow]:
    out: list[GameWindow] = []
    for w in windows or []:
        # Skip menus/overlays, but allow normal + fullscreen content layers.
        layer = int(w.get("kCGWindowLayer") or 0)
        if layer not in (0,):
            continue
        owner = w.get("kCGWindowOwnerName") or ""
        title = w.get("kCGWindowName") or ""
        if not _is_game_owner_or_title(owner, title):
            continue
        b = w.get("kCGWindowBounds") or {}
        width = int(b.get("Width") or 0)
        height = int(b.get("Height") or 0)
        if width < MIN_W or height < MIN_H:
            continue  # chrome strips / tiny helper surfaces
        out.append(GameWindow(
            x=int(b.get("X") or 0),
            y=int(b.get("Y") or 0),
            width=width,
            height=height,
            pid=int(w.get("kCGWindowOwnerPID") or 0),
            title=title or owner,
            window_id=int(w.get("kCGWindowNumber") or 0),
        ))
    return out


def _pick_largest(cands: list[GameWindow]) -> GameWindow | None:
    if not cands:
        return None
    return max(cands, key=lambda g: g.width * g.height)


def find_game() -> GameWindow | None:
    """The RuneScape client window, or None if the game is not open.

    Prefers on-screen windows; if none (typical when RS is fullscreen on
    another Space), falls back to all windows and takes the largest match.
    """
    on_screen = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    hit = _pick_largest(_candidates(on_screen))
    if hit is not None:
        return hit

    all_wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll,
        Quartz.kCGNullWindowID,
    )
    return _pick_largest(_candidates(all_wins))


def is_frontmost() -> bool:
    """True when RuneScape is the app the user is actually looking at.

    This is the main safety switch: tab away and the bot stops pressing keys.
    """
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return False
    name = (app.localizedName() or "").strip()
    return name in OWNER_NAMES
