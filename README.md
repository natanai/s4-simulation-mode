# Simulation Mode Kernel Mod (v0.5.0)

## What it is

This is a minimal Sims 4 script mod kernel that registers the `simulation` cheat-console command and runs a lightweight real-time watchdog while Simulation Mode is enabled. The watchdog can:

* Push real in-world self-care interactions (sleep/eat/toilet/shower) when household Sims are trending toward yellow/red motives.
* Block pregnancy unless explicitly allowed (to avoid naming dialogs during unattended play).
* Optionally auto-unpause if the game clock is paused.
* Toggle death on/off while Simulation Mode is enabled (reasserted periodically).

## What it is not (v0.5.0 non-goals)

* No action/event logging yet.
* No cheating motive values or filling needs.
* No complex autonomy rewrites beyond light interaction pushes.
* No attempt to override global game options (aging, etc.).
* No attempt to handle every modal dialog in the game.

## Prereqs

* Python 3 is required to build the `.ts4script` archive.

## Build

```bash
python tools/build_ts4script.py
```

The build always outputs `dist/simulation-mode.ts4script`. Versioning is tracked in
`VERSION.txt` (and optionally git tags), not in the filename. The artifact name is always
`simulation-mode.ts4script`.

### Packaging rules

The game only loads compiled `.pyc` bytecode from the archive, so the build must include
the `.pyc` files alongside the sources. Do not remove `.pyc` from the archive; the mod will
not load.

## Download from GitHub Actions

Run the “Build Simulation Mode Script” workflow and download the artifact named
`s4-simulation-mode` from the completed workflow run. The artifact contains
`dist/simulation-mode.ts4script`.

## Install

```bash
python tools/install_to_mods.py
```

Place both files in your Mods folder:

```
Mods/SimulationMode/simulation-mode.ts4script
Mods/SimulationMode/simulation-mode.txt
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
* `simulation reload`
* `simulation director`
* `simulation help`
* `simulation debug` (includes auto-unpause diagnostics)

Settings are stored in the manually editable file:

`Mods/SimulationMode/simulation-mode.txt`

After editing the TXT file, run `simulation reload` in-game to apply changes without restarting.

Notes:

* `guardian_min_motive` starts intervening when a core motive drops below this value. Motives generally range from -100..100, with yellow between -1..-50 and red below -50.
* The Life Director nudges real skill-building interactions (no motive/skill cheating) when Sims are safe and idle.
* `death.toggle` is applied on enable and reasserted periodically while Simulation Mode is running.

## Life Director settings

```text
director_enabled=true
director_check_seconds=90
director_min_safe_motive=-10
director_per_sim_cooldown_seconds=300
director_max_pushes_per_sim_per_hour=12
director_prefer_career_skills=true
director_fallback_to_started_skills=true
director_skill_allow_list=
director_skill_block_list=
```

## Test plan

1. Put `simulation-mode.ts4script` into `Mods/SimulationMode/`.
2. Add `simulation-mode.txt` to `Mods/SimulationMode/`.
3. Enable script mods.
4. In-game, run `simulation status` (should show loaded + version if provided).
5. Run `simulation true`.
6. Wait 3–5 seconds; run `simulation status` again: `tick_count` should be > 0 and `daemon_running` should be `True`.
7. Pause the game, wait 2 seconds, and confirm it unpauses. If it does not, confirm `daemon_running` is `True` and `tick_count` is increasing, then debug the clock API.
8. Let a household Sim dip into yellow motives and confirm the guardian pushes a self-care interaction.
