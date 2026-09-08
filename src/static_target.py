"""Static-target filter for pixel-based attack.

Uses the same colour blobs as ``targets.find_zombies``, but only returns a blob
once it has stayed within a small radius for ``still_s`` seconds (default ~2).
Moving enemies are ignored until they stop.

This module does not detect blobs itself — callers pass the pixel target list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .targets import Target

DEFAULT_STILL_S = 2.0
DEFAULT_MATCH_RADIUS_PX = 28
# Fraction of match radius that counts as a real move (centroid noise below this
# keeps the stillness clock running).
DEFAULT_MOVE_FRAC = 0.35
# Drop tracks that disappear for this long (seconds).
_LOST_AFTER_S = 1.0


@dataclass
class _Track:
    """One persistent blob identity across frames."""

    x: int
    y: int
    area: int
    first_seen_at: float
    last_moved_at: float
    last_seen_at: float
    still_since: float


@dataclass
class StaticTargetTracker:
    """Associate pixel blobs frame-to-frame and keep only the still ones."""

    still_s: float = DEFAULT_STILL_S
    match_radius_px: int = DEFAULT_MATCH_RADIUS_PX
    move_frac: float = DEFAULT_MOVE_FRAC
    _tracks: list[_Track] = field(default_factory=list)

    def reset(self) -> None:
        """Clear all motion history (call when leaving static mode)."""
        self._tracks.clear()

    def configure(self, targeting_cfg: dict | None) -> None:
        """Refresh still / match radii from ``targeting`` config."""
        tcfg = targeting_cfg if isinstance(targeting_cfg, dict) else {}
        self.still_s = float(tcfg.get("static_still_s", DEFAULT_STILL_S))
        self.match_radius_px = int(
            tcfg.get("static_match_radius_px", DEFAULT_MATCH_RADIUS_PX))
        self.move_frac = float(tcfg.get("static_move_frac", DEFAULT_MOVE_FRAC))
        self.move_frac = max(0.15, min(0.9, self.move_frac))

    def update(self, found: list[Target], now: float) -> list[Target]:
        """Update tracks from this frame's pixel blobs; return still targets.

        A blob is "still" when it has been matched continuously and has not
        moved more than ``match_radius_px`` for at least ``still_s`` seconds.
        """
        r2 = self.match_radius_px * self.match_radius_px
        move_r2 = (self.match_radius_px * self.move_frac) ** 2
        used: set[int] = set()

        for blob in found:
            best_i: int | None = None
            best_d2 = r2 + 1
            for i, tr in enumerate(self._tracks):
                if i in used:
                    continue
                d2 = (blob.x - tr.x) ** 2 + (blob.y - tr.y) ** 2
                if d2 <= r2 and d2 < best_d2:
                    best_d2 = d2
                    best_i = i
            if best_i is None:
                self._tracks.append(_Track(
                    x=blob.x, y=blob.y, area=blob.area,
                    first_seen_at=now, last_moved_at=now,
                    last_seen_at=now, still_since=now,
                ))
                continue
            used.add(best_i)
            tr = self._tracks[best_i]
            if best_d2 > move_r2:
                # Noticeable shift — reset stillness clock, keep identity.
                tr.last_moved_at = now
                tr.still_since = now
            tr.x, tr.y, tr.area = blob.x, blob.y, blob.area
            tr.last_seen_at = now

        # Drop tracks that vanished this frame for too long.
        self._tracks = [
            tr for tr in self._tracks
            if (now - tr.last_seen_at) <= _LOST_AFTER_S
        ]

        still: list[Target] = []
        for tr in self._tracks:
            if (now - tr.last_seen_at) > 1e-3:
                continue  # not visible this frame
            if (now - tr.still_since) >= self.still_s:
                still.append(Target(x=tr.x, y=tr.y, area=tr.area))
        return still

    def filter_targets(
        self,
        found: list[Target],
        now: float,
        targeting_cfg: dict | None = None,
    ) -> list[Target]:
        """Configure from cfg, update tracks, return only static blobs."""
        self.configure(targeting_cfg)
        return self.update(found, now)
