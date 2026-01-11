# Simulation Mode Kernel Mod (v0.2)

## What it is

This is a minimal Sims 4 script mod kernel that registers the `simulation` cheat-console command and runs a lightweight real-time watchdog while Simulation Mode is enabled. The watchdog can:

* Keep household Sims alive by bumping critical motives when they fall below a floor.
* Block pregnancy unless explicitly allowed (to avoid naming dialogs during unattended play).
* Optionally auto-unpause if the game clock is paused.

## What it is not (v0.2 non-goals)

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

## Install

```bash
python tools/install_to_mods.py
```

The `.ts4script` must be no deeper than one subfolder in your Mods folder.

## Enable script mods

In-game: Options → Game Options → Other → enable “Enable Custom Content and Mods” and “Script Mods Allowed”, then restart the game.

## Use

Open the cheat console and run:

* `simulation status`
* `simulation true`
* `simulation false`
* `simulation allow_pregnancy true|false`
* `simulation protect_motives true|false`
* `simulation auto_unpause true|false`
* `simulation tick <seconds>` (clamped to 2..120)

Pregnancy is blocked by default while Simulation Mode is enabled. Motive protection only bumps critical motives upward when they dip below a floor; it does not max all needs.
