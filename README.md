# Simulation Mode Kernel Mod

## What it is

This is a minimal Sims 4 script mod kernel that registers the `simulation` cheat-console command and prints the current Simulation Mode status.

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

* `simulation`
* `simulation true`
* `simulation false`
