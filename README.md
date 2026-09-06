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
| **Pixel probes** | One click each for health / adrenaline / target bar — no fragile “draw a box on the HP bar” calibration loop |
| **Smart retarget** | Waits for the top target bar to clear, cools down, then picks the densest enemy blob |
| **Human mode** | Curved mouse paths + occasional wander while waiting — clicks still land on the **exact** pixel |

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

1. Place a **cyan V** ground marker on the room floor  
2. `mark.py` → draw **search box** → **fence** → **zombie colours** → **HP / adren / target probes** → save  
3. `run.py` → it locks, eats, clicks, wanders like a person (if you want)

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
| **`t`** | Target-bar probe — engage a mob, click a pixel on the top target bar |
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
| Overlay **Attack** | `bot` = snappy · `human` = curved mouse + wander while waiting |
| Overlay **Eat** | `low HP` always · `in combat` only while fighting / target bar |
| `b` / `e` | Toggle attack / eat mode |
| `F12` | Pause / resume |
| Esc ×3 | Stop |
| `q` | Stop (overlay focused) |

---

## Area of Interest — how it actually works

This is the feature people remember.

1. **`v` search box** — “Where am I allowed to *look* for the cyan V?” (UI stays out.)  
2. **`a` fence** — “Where am I allowed to *click* enemies?” Stored as **offsets from the V**, not fixed screen pixels.  
3. At runtime the bot finds the V every frame (template + cyan mask **inside** your search box) and **rebuilds the green fence** around it.  
4. Walk around the room → fence follows the floor marker. Leave the marker → fence lost → no random clicks on chat/minimap.

Re-run `mark.py` after big zoom / camera changes.

---

## Pixel probes (no more bar-box hell)

| Probe | You click… | Bot treats… |
|-------|------------|-------------|
| **Health (`h`)** | Red fill at your eat threshold | Colour **changes** → press food (`0`) |
| **Adrenaline (`d`)** | Empty adren track | Colour **changes** → fighting |
| **Target bar (`t`)** | Top target-info bar while engaged | Colour **matches** → under attack / hold fire |

Saved in `config.json` under `probes`. Next `mark.py` run **reloads** them — only re-click what you want to change.

---

## Human mode (optional)

When **Attack → human** is on:

- **Clicks** use a visible curved path, then button events fire on the **exact** target pixel  
- **While waiting** on target bar / cooldown, the mouse **wanders every few seconds** inside the AOI — not a twitch every tick  
- **RuneScape must stay frontmost** or everything pauses (including wander)

---

## Project layout

```
run.py              start the loop
mark.py             V + search box + fence + colours + probes
keytest.py          prove Accessibility works
calibrate.py        optional legacy bar boxes
Guide.md            full first-machine walkthrough
config.default.json all tunables (catalogue)
config.json         your live mark (created on save — not in git)

src/
  aoi.py            cyan-V lock + fence rebuild
  probes.py         single-pixel health / adren / target
  targets.py        colour blobs inside the fence
  human.py          curved mouse + wait wander
  bot.py            decisions + loop
  capture.py        CG window grab (works across Spaces)

reference/markers/cyan_marker_crop.png   required V template
```

---

## Config knobs worth knowing

Edit `config.json` after first save (or copy from `config.default.json`).

| Key | Meaning |
|-----|---------|
| `probes.*` | Health / adren / target-bar pixels + tolerance |
| `targeting.target_bar_arm_s` | Seconds to wait for bar after a click (default **3**) |
| `targeting.retarget_cooldown_s` | Seconds after a kill before next click (default **3**) |
| `targeting.zombie_colors_bgr` | Include colours from `mark.py` |
| `aoi.search_rect` | Your `v` box (also clips the AOI crop away from HUD) |
| `aoi.polygon_offset` | Fence corners relative to the cyan V |
| `human.wait_wander_interval_s` | How often to wander while waiting (`[2.5, 6.0]`) |
| `behavior.attack_style` | `bot` \| `human` |
| `ability_keys` | e.g. `["1","2"]` — **empty = press nothing** |
| `food_key` | Default `"0"` |

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
| MARKER LOST | Cyan V on floor, blinking/visible; re-run `mark.py` with `v` box tight |
| Action bar in AOI window | Tighten `v` search box above the HUD; AOI crop is clipped to it |
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
