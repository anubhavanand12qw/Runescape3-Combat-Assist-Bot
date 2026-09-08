# First-time setup guide (new machine)

This walks you from an empty Mac to a working dry-run: Screen Recording,
Accessibility, dependencies, the **cyan V ground marker**, `mark.py`, then
`run.py --dry-run`.

> **Warning:** Jagex does not allow bots. Using this can get your account banned.

---

## Config (one place)

Edit **`config.json`** for everything tunable: health thresholds, target-bar
ROI/BGR colours, cyan V HSV, blob sizes, cooldowns, ability keys.
`config.default.json` is the full catalogue; at load it is merged under your
live `config.json` so new knobs appear automatically.

| Need | Why |
|---|---|
| macOS + Python 3.10+ | This project uses Quartz + OpenCV |
| RuneScape 3 open, logged in | Window capture + key/click targets |
| A **cyan V** ground / tile marker in the fight room | World-locks the fence (not your character) |
| Terminal you will run the bot from | Must be granted Screen Recording + Accessibility |

The V must sit on the **room floor**, not under your feet forever and not only
visible on the minimap. Prefer a bright cyan **V** shape that blinks ON/OFF.

---

## 1. Copy the project

```bash
cd ~/Desktop/"Python Files"   # or wherever you keep projects
# clone / copy the "RS3 Runescape Assistant Bot" folder here
cd "RS3 Runescape Assistant Bot"
```

Confirm the V template exists (ships with the repo):

```bash
ls -la reference/markers/cyan_marker_crop.png
```

If that file is missing, the bot **will not** lock (and `mark.py` will refuse to
save). Put an 80×80-ish crop of the cyan V at that path. Optional extras:

- `cyan_marker_closeup.png` — larger reference photo
- `cyan_marker_room.png` — full room context (docs only)

---

## 2. Install Python packages

```bash
python3 -m pip install -r requirements.txt
```

Packages: `mss`, `numpy`, `opencv-python`, `pynput`, `pyobjc-framework-Quartz`.

---

## 3. macOS permissions (everyone gets stuck here)

**System Settings → Privacy & Security**

1. **Screen Recording** → enable the **exact** app you use to run Python
   (Terminal, iTerm, Cursor, VS Code, etc.).
2. **Accessibility** → enable that same app (keys + mouse clicks).

Then **fully quit** that app (Cmd-Q) and open it again. macOS caches denials;
without a restart, scripts silently do nothing.

---

## 4. Prove keys work

Open RuneScape, then:

```bash
python3 keytest.py
```

You get a few seconds to click the game, then it taps `1`.

- Ability fires or chat says “Ability not ready yet” → OK.
- Nothing happens → try `python3 keytest.py 1 --pid`, then re-check Accessibility
  and restart the terminal app.

---

## 5. Mark room + UI probes (do this once per layout)

```bash
python3 mark.py
```

**Room lock** (when the fight area changes): cyan V on the floor → `v` search box →
`a` fence → `z` zombie colours.

**UI probes** (persist across runs — only re-click what you want to change):

| Key | Click | Runtime meaning |
|---|---|---|
| `h` | Red health fill at your eat threshold | Colour **changes** → press food (default `[`) |
| `d` | Empty adrenaline bar (0%) | Colour **changes** → fighting |
| `t` | Top target-info bar while engaged (pick a distinctive pixel) | Colour **matches** → under attack / kill count |
| `l` | Loot UI button (optional) | Used when Loot **always** + Inv box **on** |
| `i` | Blank inventory slot (optional) | Same as `l` |

Then `s` to save. Next `mark.py` run reloads probes automatically.

Keep `probes.tolerance` fairly tight (around **10**) so idle background is not mistaken for the target bar.

`run.py` skips bar auto-cal when probes are set.

**Method → free** does not need the cyan V / fence; still mark zombie colours (`z`) and probes (`h`/`d`/`t`).

---

## 6. Place the cyan V in the game (mandatory)

In RS3:

1. Go to the fight room.
2. Place your **cyan V** ground marker on open floor inside the room.
3. Leave camera zoom / angle as you will fight (changing zoom later requires
   re-running `mark.py`).
4. Make sure the V is visible in the 3D world (not only as a minimap blob).

The bot matches the **V shape** on a cyan mask. Random cyan chat text, UI
highlights, or minimap dots must **not** be used as the lock — that is why the
search box exists (next step).

---

## 7. Mark fence + colours (`mark.py`) — mandatory checklist

```bash
python3 mark.py
```

Countdown → switch to RuneScape → freeze when the cyan V is ON (blink catch).

The banner shows a checklist. Do this **exact order**:

| Step | Key | What to do | Ready when |
|---|---|---|---|
| 1 | (in game) | Cyan V visible on floor | Banner: `[x] cyan V visible` |
| 2 | **`v`** | Click **two opposite corners** of a rectangle that covers the playfield. Keep **minimap, action bar, and chat outside** the orange SEARCH box. | `[x] search box (v)` |
| 3 | **`a`** | Left-click **3+** fence corners around the kill zone. Right-click deletes; `i` inserts on an edge. | `[x] fence 3+ (a)` |
| 4 | **`z`** | Left-click zombie **body** pixels (magenta include). Right-click bones/floor to **exclude**. Adjust `+/-` tolerance until magenta sits on zombies only. | `[x] zombie colour (z)` |
| 5 | **`s`** | Save. Blocked until search box, fence, colours, and V are all set. | Checklist line turns green |

Other useful keys: `r` refresh freeze, `u` undo, `c` clear current mode, `x` clear all, `q` quit without save, `[` `]` dead-zone size.

### What gets saved

Into `config.json`:

- `aoi.lock_mode`: `"cyan_marker"`
- `aoi.polygon_offset`: fence corners relative to the V
- `aoi.search_rect`: where the V may be found (fractions 0–1)
- `aoi.marker.template_path` + `template_threshold` (default `0.62`)
- `targeting.zombie_colors_bgr` / `zombie_exclude_bgr`

---

## 8. Dry-run (no clicks / no combat keys unless you already enabled them)

```bash
python3 run.py --dry-run
```

Check the **RS3 AOI** overlay:

1. Yellow / marker cross sits on the **floor V**, not the minimap.
2. Green fence stays on the **room** when you walk a few steps (V still visible).
3. Magenta / cyan zombie blobs sit on enemies, not walls or your character.
4. Health % matches the game.
5. Overlay shows **TARGET BAR** while fighting (top dark bar visible) — no new click.
6. When the top bar disappears and HP > 50%, status shows **READY** and the pick is the **largest** colour blob (not tiny loot).

If the fence follows **you**, the detector is still using UI cyan — redraw a
tighter **`v` search box** (exclude minimap) and save again.

---

## 9. Live run

```bash
python3 run.py
```

Eats when the health probe changes (`food_key` default `"["`). After two failed
eats, **SAFE STOP** pauses loot, bury, and combat until HP stays OK briefly.

Clicks when the target-bar probe is clear (and HP OK), with a post-clear watch
window before the next click. Prefer **nearest-to-centre** blobs when configured.
Pre-click pixel verify aborts if the NPC colour walked away.

Loot (Space) and bury (`]`) modes: `off` / after combat / always — see overlay.
Food, loot, and bury always use human key timing.

Overlay shows **kills** and **kills/hr** from target-bar clear count this session.

To enable abilities later, edit `config.json`:

```json
"ability_keys": ["1", "2"]
```

Leave it `[]` until dry-run looks solid.

---

## 10. Hotkeys while running

| Key | Action |
|---|---|
| Overlay clicks / `b` `m` `e` `o` `i` `u` `s` | Attack, method, eat, loot, inv, bury, free still |
| Overlay `+/−` rows | Bar wait, hard hold, still time, bar arm, loot/bury gaps |
| `F12` | pause / resume |
| Esc ×3 quickly | stop |
| `q` (overlay focused) | stop |
| Ctrl-C | stop |

Bot auto-pauses when RuneScape is not the front window.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Keys/clicks do nothing | Accessibility + fully restart terminal; try `keytest.py --pid` |
| Black / empty capture | Screen Recording for the same app; restart app |
| `NEED mark.py` / no fence | Re-run `mark.py` and save with `polygon_offset` |
| `MARKER LOST` | V off-screen, wrong zoom, or search box too small — `r` in mark / re-mark |
| Fence follows player | Search box includes minimap — press `v`, keep minimap **out**, save |
| Yellow cross on minimap | Same as above; V must be the floor template, not UI cyan |
| Clicks bones / floor / loot | Densest blob should prefer enemies; raise `min_blob_area`, re-sample `z` on body only, right-click exclude bones |
| Clicks while already fighting | Target probe still matching — remake `t`, lower `probes.tolerance` |
| FREE hard-limit spam | Sticky target probe or fights longer than hard hold — fix `t` or raise Hard hold |
| Never clicks after a kill | HP probe / SAFE STOP / cooldown watch — check overlay status |
| SAFE STOP stuck | Restock food; remake health probe `h`; wait for HP OK hold |
| Space / ] during cooldown | Expected mute during bar wait / human break / SAFE STOP |
| Clicks your character | Raise `aoi.deadzone_frac` (e.g. `0.07`) |
| HP % wrong | Prefer probes (`h`); else `python3 calibrate.py` |
| After camera zoom fence drifts | Re-run `mark.py` (offsets are zoom-specific) |
| Save blocked: missing crop | Restore `reference/markers/cyan_marker_crop.png` |
| Want no cyan V | Overlay Method → **free** |

---

## How the V lock works (short)

1. Capture game window each tick.
2. Crop to `search_rect` (or a default playfield if somehow unset).
3. Build a **cyan binary mask** (HSV range).
4. `matchTemplate` the **V crop mask** at a few scales; score must be ≥ threshold.
5. Fence = marker centre + saved `polygon_offset`.
6. Brief OFF blinks are held over; long absence → fence lost.

Generic cyan **blobs** and gray image matching are **disabled** so chat/minimap
text cannot steal the lock.

---

## Checklist — brand new machine

- [ ] Project folder copied
- [ ] `reference/markers/cyan_marker_crop.png` present
- [ ] `pip install -r requirements.txt`
- [ ] Screen Recording + Accessibility for your terminal app (then restart app)
- [ ] `keytest.py` works
- [ ] `calibrate.py` (or trust auto-cal on first `run.py`)
- [ ] Cyan V placed on room floor in RS3
- [ ] `mark.py`: **v** → **a** → **z** → **s**
- [ ] `run.py --dry-run` looks correct
- [ ] `run.py` live when ready
