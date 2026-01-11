# Simulation Mode Kernel Mod (v0.3.0)

## What it is

This is a minimal Sims 4 script mod kernel that registers the `simulation` cheat-console command and runs a lightweight real-time watchdog while Simulation Mode is enabled. The watchdog can:

* Keep household Sims alive by bumping critical motives when they fall below a floor.
* Block pregnancy unless explicitly allowed (to avoid naming dialogs during unattended play).
* Optionally auto-unpause if the game clock is paused.
* Optionally auto-respond to dialogs (best effort; may fail on game updates).
* Toggle death on/off while Simulation Mode is enabled (reasserted periodically).

## What it is not (v0.3.0 non-goals)

* No action/event logging yet.
* No complex autonomy rewrites or interaction injection beyond pregnancy blocking.
* No attempts to override global game options (aging, etc.).
* No attempt to handle every modal dialog in the game.

## Prereqs

* Python 3.7 is required to build a `.pyc` compatible with the game’s script environment and common Sims 4 script-mod toolchains.

## Build

```bash
python tools/build_ts4script.py
```

The build always outputs `dist/simulation-mode.ts4script`. Versioning is tracked in
`VERSION.txt` (and optionally git tags), not in the filename. The artifact name is always
`simulation-mode.ts4script`.

## Download from GitHub Actions

Run the “Build Simulation Mode Script” workflow and download the artifact named
`s4-simulation-mode` from the completed workflow run. The artifact contains
`dist/simulation-mode.ts4script`.

## Install

```bash
python tools/install_to_mods.py
```

The `.ts4script` must be no deeper than one subfolder in your Mods folder.

If the `simulation` command does not register, verify the archive contains a root
`s4_simulation_mode.py` file alongside the `simulation_mode/` package.

## Enable script mods

In-game: Options → Game Options → Other → enable “Enable Custom Content and Mods” and “Script Mods Allowed”, then restart the game.

## Use

Open the cheat console and run:

* `simulation status`
* `simulation true`
* `simulation false`
* `simulation help`
* `simulation set auto_unpause true|false`
* `simulation set auto_dialogs true|false`
* `simulation set allow_death true|false`
* `simulation set allow_pregnancy true|false`
* `simulation set tick <seconds>` (clamped to 2..120)
* `simulation reload`
* `simulation preset safe|chaos`

Pregnancy is blocked by default while Simulation Mode is enabled. Motive protection only bumps critical motives upward when they dip below a floor; it does not max all needs.

Settings are persisted to:

`Documents/Electronic Arts/The Sims 4/mod_data/simulation-mode/settings.json`

Notes:

* `auto_dialogs` runs the `ui.dialog.auto_respond` cheat when Simulation Mode is enabled, but it may fail on some patches.
* `death.toggle` is applied on enable and reasserted periodically while Simulation Mode is running.

## Test plan

1. Put `simulation-mode.ts4script` into `Mods/SimulationMode/`.
2. Enable script mods.
3. In-game, run `simulation status` (should show loaded + version if provided).
4. Run `simulation set tick 1`, then `simulation true`.
5. Wait 3–5 seconds; run `simulation status` again: `tick_count` should be > 0 and `daemon_running` should be `True`.
6. Pause the game, wait 2 seconds, and confirm it unpauses. If it does not, confirm `daemon_running` is `True` and `tick_count` is increasing, then debug the clock API.
