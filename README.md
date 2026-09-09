# Runescape3 Combat Assist Bot

### Pixel vision. World-locked fence. Human-like hands.

macOS assistant for **RuneScape 3** that watches your screen — not the game API — and helps you stay alive and on-target inside a fight room you draw yourself.

> ⚠️ **Jagex does not allow bots.** Using this can get your account banned. Use at your own risk. Educational / personal tooling only.

---

## Why people care

| Superpower | What it means in plain English |
|---|---|
| **Cyan-V world lock** | Your fight fence sticks to a **cyan V on the floor**, so it stays on the *room* when you walk — not glued to your character or the minimap |
| **Area of Interest** | You paint the kill zone once (`mark.py`). The bot only hunts enemies **inside that green fence** |
| **Free method** | Optional: click visible zombie colours with **no cyan marker / fence** — target-bar gated, optional stillness |
| **Pixel probes** | One click each for health / adrenaline / target bar — no fragile “draw a box on the HP bar” calibration loop |
| **Smart retarget** | Waits for the top target bar, cools down (random watch), skips click if bar returns on its own |
| **Loot + bury** | Space and `]` with human timing — `off` / after combat / always (independent intervals) |
| **Eat SAFE STOP** | Two failed eats → pause loot, bury, and combat until HP stays OK |
| **Kills / hour** | Counts target-bar clears this session; shown on the overlay |
| **Human mode** | Curved mouse paths + wander while waiting — clicks still land on the **exact** pixel |

---

## Demo of the idea

```
  ┌──────────────────────── RuneScape window ────────────────────────┐
  │  minimap (ignored)                          [target bar]         │
  │                                                                  │
  │         ┌─────── AOI fence (green) ───────┐                      │
  │         │   🧟  🧟                         │                      │
  │         │        ✦ cyan V (world lock)     │                      │
  │         │              you                 │                      │
  │         └──────────────────────────────────┘                      │
  │  chat                    ♥ HP  ⚔ adren  action bar               │
  └──────────────────────────────────────────────────────────────────┘
```

1. Place a **cyan V** ground marker on the room floor *(not required for Method → free)*  
2. `mark.py` → draw **search box** → **fence** → **zombie colours** → **HP / adren / target probes** → save  
3. `run.py` → lock, eat, click, loot/bury, wander like a person (if you want)

---

## Quick start (5 minutes)

**Needs:** macOS + Python 3.10+ + RuneScape 3 open

```bash
git clone https://github.com/anubhavanand12qw/Runescape3-Combat-Assist-Bot.git
cd Runescape3-Combat-Assist-Bot

python3 -m pip install -r requirements.txt
```

### 1) macOS permissions (do this once)

**System Settings → Privacy & Security**

1. **Screen Recording** → enable the app that runs Python (Terminal / iTerm / Cursor / VS Code)  
2. **Accessibility** → enable that same app (for keys + mouse)  
3. **Fully quit** that app (Cmd-Q) and reopen it  

### 2) Prove keys work

```bash
python3 keytest.py
```

Click into RuneScape when prompted. You should see ability / chat feedback.

### 3) Mark your room (the important part)

In RS3: put a **cyan V** on the **fight-room floor** (not under your feet forever, not only on the minimap).

```bash
python3 mark.py
```

| Key | What you do |
|-----|-------------|
| **`v`** | Search box — 2 corners around the playfield (**keep minimap / action bar OUT**) |
| **`a`** | Area fence — 3+ corners around the kill zone |
| **`z`** | Zombie colours — left-click body, right-click bones/floor to exclude |
| **`h`** | Health probe — click red bar at the % where you want to eat |
| **`d`** | Adrenaline probe — click the **empty** adren bar (0%) |
| **`t`** | Target-bar probe — engage a mob, click a **distinctive** pixel on the top target bar (keep `probes.tolerance` tight, e.g. ~10) |
| **`l`** | Loot-button probe — open loot UI, click the loot button (optional; for **always** + Inv box on) |
| **`i`** | Blank-inventory probe — click an empty inv slot (optional) |
| **`s`** | Save (probes persist — next time you only re-mark what changed) |
| **`r`** | Refresh freeze if the V blinked off |

### 4) Dry run, then live

```bash
python3 run.py --dry-run    # watch overlays, no keys/clicks
python3 run.py              # for real
```

**While it runs:** keep the **RuneScape window frontmost** or it pauses (safety).

| Control | Action |
|---------|--------|
| Overlay **Attack** | `bot` · `human` (curved mouse + wander) · `off` (eat/loot/bury still run) |
| Overlay **Method** | `pixel` · `static` (fence + still) · `free` (visible colours, no marker; optional still) |
| Overlay **Eat** | `low HP` · `in combat` |
| Overlay **Loot** | `off` · `target` (Space when bar clears) · `always` (random gap, default 2–5s) |
| Overlay **Bury** | `off` · `target` · `always` — key `]` (human timing) |
| Overlay **Inv box** | `off` = Space only · `on` = always also uses loot probes (`l`/`i`) |
| Overlay **Free still** | `off` by default — skip 2s stillness in free mode (pre-click pixel verify still runs) |
| Overlay **Cyan avoid** | `off` by default — before click, skip aim if cyan in `Cyan box` (default 100×100); hotkey `c` |
| Overlay **Rotate** | `off` by default — Right Arrow hold (`Rot hold` 0.5–2s) every `Rot gap` (3–10s); hotkey `r` |
| Overlay **+/−** | Bar wait, hard hold, still time, bar arm, loot gap, bury gap — persisted to `config.json` |
| Overlay **kills / hr** | Target-bar clear count this session |
| `b` / `m` / `e` / `o` / `i` / `u` / `s` | Cycle attack / method / eat / loot / inv / bury / free still |
| `F12` | Pause / resume |
| Esc ×3 | Stop |
| `q` | Stop (overlay focused) |

---

## Area of Interest — how it actually works

1. **`v` search box** — “Where am I allowed to *look* for the cyan V?” (UI stays out.)  
2. **`a` fence** — “Where am I allowed to *click* enemies?” Stored as **offsets from the V**, not fixed screen pixels.  
3. At runtime the bot finds the V every frame (template + cyan mask **inside** your search box) and **rebuilds the green fence** around it.  
4. Walk around the room → fence follows the floor marker. Leave the marker → fence lost → no random clicks on chat/minimap.

**Method → free** skips the marker/fence and clicks colour matches in the visible playfield (no morph / density filter; small `free_min_match_area`), still gated by the target-bar probe. `pixel` / `static` keep the denser blob pipeline.

Re-run `mark.py` after big zoom / camera changes.

---

## Pixel probes (no more bar-box hell)

| Probe | You click… | Bot treats… |
|-------|------------|-------------|
| **Health (`h`)** | Red fill at your eat threshold | Colour **changes** → press food (default `[`) with **human** key timing |
| **Adrenaline (`d`)** | Empty adren track | Colour **changes** → fighting |
| **Target bar (`t`)** | Top target-info bar while engaged | Colour **matches** → under attack / hold fire / kill count |
| **Loot button (`l`)** | Loot UI button while visible | Colour **matches** → loot present (`always` + Inv on) |
| **Loot inv (`i`)** | Blank inventory slot | Colour **changes** → loot present (`always` + Inv on) |

Saved in `config.json` under `probes`. Next `mark.py` run **reloads** them — only re-click what you want to change.

Food, loot, and bury **always** use `human.*` key delays (even when Attack is `bot` or `off`).

---

## Combat assist behaviour (summary)

- **Retarget:** after a click, wait for the bar; after bar clear, random **bar wait** watch — if the bar returns alone, don’t click elsewhere. Free mode has a **hard hold** cap then force a different target.
- **Pre-click verify:** abort click if zombie colour is gone under the cursor.
- **Nearest:** prefer blob nearest screen/AOI centre when `click_nearest` is true.
- **Eat SAFE STOP:** after `eat_max_failures` (default 2) failed eats → pause loot, bury, and combat until HP stays OK for `eat_resume_hold_s`.
- **Support-key mute:** Space / bury do not fire during eat SAFE STOP, human micro-breaks, or post-click / cooldown waits.
- **Kills/hr:** each target-bar ON→OFF increments the session counter (overlay + stop summary).

---

## Human mode (optional)

When **Attack → human** is on:

- **Clicks** use a visible curved path, then button events fire on the **exact** target pixel  
- **While waiting** on target bar / cooldown, the mouse **wanders every few seconds** — not a twitch every tick  
- **RuneScape must stay frontmost** or everything pauses (including wander)

When **Attack → off**: no targeting clicks and no ability keys; eat, loot, and bury still work.

---

## Project layout

```
run.py              start the loop
mark.py             V + search box + fence + colours + probes
keytest.py          prove Accessibility works
calibrate.py        optional legacy bar boxes
Guide.md            full first-machine walkthrough
config.default.json all tunables (catalogue)
config.json         your live mark (created/updated on save)

src/
  aoi.py            cyan-V lock + fence rebuild
  free_pixel.py     free-method colour targeting
  static_target.py  stillness filter for static/free
  probes.py         single-pixel health / adren / target / loot
  targets.py        colour blobs + pre-click colour check
  loot.py           Space loot decisions
  bury.py           bury-key decisions
  human.py          curved mouse + wait wander + short breaks
  bot.py            decisions + loop
  overlay.py        status panel + clickable controls
  capture.py        CG window grab (works across Spaces)

reference/markers/cyan_marker_crop.png   required V template (pixel/static)
```

---

## Config knobs worth knowing

Edit `config.json` after first save (or copy from `config.default.json`). Overlay **+/−** and toggles persist live via `config_util.patch_live`.

| Key | Meaning |
|-----|---------|
| `probes.*` | Health / adren / target-bar pixels + **`tolerance`** (try ~10 if bar sticks) |
| `food_key` | Default `"["` |
| `bury.bury_key` | Default `"]"` |
| `targeting.target_bar_arm_s` | Seconds to wait for bar after a click |
| `targeting.retarget_cooldown_s` | `[lo, hi]` watch after bar clears (e.g. `[4.5, 6]`) |
| `targeting.free_max_bar_hold_s` | Free-mode hard hold before force retarget (`[lo, hi]` seconds, rolled per engagement) |
| `targeting.free_min_match_area` | Free only: min colour-cluster size (default 12; zoom-out friendly) |
| `targeting.cyan_avoid_enabled` | Pre-click: abort if cyan trap colour in `cyan_avoid_size_px` box (default off) |
| `targeting.cyan_avoid_size_px` | Cyan check window (default 100 → 100×100) |
| `behavior.rotate_screen` | Periodic camera rotate via Right Arrow (default off) |
| `targeting.rotate_interval_s` | Seconds between rotates (`[lo, hi]`, default 3–10) |
| `targeting.rotate_hold_s` | Right Arrow hold (`[lo, hi]`, default 0.5–2) |
| `targeting.static_still_s` | Stillness seconds (static method; free if Free still on) |
| `targeting.click_nearest` | Prefer nearest-to-centre blob |
| `behavior.attack_style` | `bot` \| `human` \| `off` |
| `behavior.attack_method` | `pixel` \| `static` \| `free` |
| `behavior.free_require_still` | `false` = free skips 2s still wait (default) |
| `behavior.loot_mode` / `bury_mode` | `off` \| `after_combat` \| `always` |
| `behavior.loot_use_inv` | `false` = Space only · `true` = probe-gated always |
| `loot.always_interval_s` / `bury.always_interval_s` | Random gaps for always mode |
| `eat_max_failures` / `eat_resume_hold_s` | SAFE STOP after failed eats |
| `ability_keys` | e.g. `["1","2"]` — **empty = press nothing** |

Full catalogue: **`config.default.json`**. Deep-merged under your live `config.json`.

---

## Flags

```bash
python3 run.py --dry-run       # read + overlay only
python3 run.py --no-overlay    # hide status panel
python3 run.py --no-aoi        # hide AOI window
python3 run.py --skip-autocal  # skip legacy bar auto-measure
```

---

## Requirements

- macOS (Quartz window capture + input)  
- Python 3.10+  
- Packages in `requirements.txt`: `mss`, `numpy`, `opencv-python`, `pynput`, `pyobjc-framework-Quartz`

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| “RuneScape is not open” | Client running; Screen Recording enabled; restart terminal app |
| Keys/clicks do nothing | Accessibility ON + full restart of that app; try `keytest.py` |
| `PAUSED - game not in front` | Click into the **RuneScape** window (overlays pause input on purpose) |
| MARKER LOST | Cyan V on floor, blinking/visible; re-run `mark.py` with `v` box tight (or use Method → free) |
| Action bar in AOI window | Tighten `v` search box above the HUD |
| Hard-limit / sticky TARGET | Remake `t` on a unique bar pixel; lower `probes.tolerance` |
| SAFE STOP | Out of food / HP probe not recovering — restock or remake `h` |
| Human mouse never moves | Same pause rule — RS must be frontmost |
| Save blocked in `mark.py` | Need fence + colours + probes (probes auto-kept from last save) |

Deeper walkthrough: **[Guide.md](Guide.md)**

---

## Disclaimer

This project reads pixels and synthesises input. It is **not** affiliated with Jagex. Botting violates the RuneScape rules. You are responsible for how you use it.

---

<p align="center">
  <b>Draw the room. Lock the V. Let the fence follow the fight.</b><br/>
  <sub>Star the repo if the cyan marker clicked for you.</sub>
</p>
