"""Load and deep-merge bot configuration.

``config.default.json`` is the single catalogue of every tunable default.
``config.json`` overlays live values (regions, fence, colours you marked).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("config.json")
DEFAULTS_PATH = Path("config.default.json")


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` with ``overlay`` applied. Nested dicts merge; other values replace."""
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if key.startswith("_") and key not in ("_note",):
            # Keep overlay comments/notes if present; still merge.
            pass
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_defaults() -> dict[str, Any]:
    """Load the full defaults catalogue, or ``{}`` if the file is missing."""
    if not DEFAULTS_PATH.exists():
        return {}
    with open(DEFAULTS_PATH) as f:
        return json.load(f)


def load_merged(path: Path | None = None) -> dict[str, Any] | None:
    """Load live config merged on top of ``config.default.json``.

    Prefers ``config.json`` when present; otherwise uses defaults alone.
    Returns ``None`` only when neither file exists.
    """
    defaults = load_defaults()
    live_path = path or (CONFIG_PATH if CONFIG_PATH.exists() else None)
    if live_path is None:
        if not defaults:
            return None
        return defaults
    with open(live_path) as f:
        live = json.load(f)
    if not defaults:
        return live
    return deep_merge(defaults, live)
