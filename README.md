# Simulation Mode Kernel Mod (v0.5.0, Build 64)

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

Drop both files in your Mods folder:

```
Mods/SimulationMode/simulation-mode.ts4script
Mods/SimulationMode/simulation-mode.txt
```

The `.ts4script` must be no deeper than one subfolder in your Mods folder.

If the `simulation` command does not register, verify the archive contains a root
`s4_simulation_mode.py` file alongside the `simulation_mode/` package.

## Enable script mods

In-game: Options → Game Options → Other → enable “Enable Custom Content and Mods” and “Script Mods Allowed”, then restart the game.

## How to run

1. Start the game and load a household.
2. Run: `simulation true`

All commands are exposed under `simulation` (and the alias `simulation_mode`).

### Command reference (examples)

| Command | Example |
| --- | --- |
| Enable Simulation Mode | `simulation true` |
| Disable Simulation Mode | `simulation false` |
| Reload settings | `simulation reload` |
| Collect snapshot log | `simulation collect` |
| Force object scan | `simulation force_scan` |
| Trigger skill plan | `simulation skill_plan_now` |
| Status | `simulation status` |

Notes:

* The Life Director nudges real skill-building interactions (no motive/skill cheating) when Sims are safe and idle.
* `simulation skill_plan_now` will defer with a short retry if the Sim is already running a different interaction.
* `death.toggle` is applied on enable and reasserted periodically while Simulation Mode is running.

## Testing workflow (for development)

Expected artifacts:

* `simulation-mode-collect.log`
* `simulation-mode-story.log`
* `simulation-mode-object-catalog.jsonl`
* `simulation-mode-capabilities.json`

`skill_plan_now` behavior: “Attempts a single strict skill-building push using the capability index (career skills preferred; else started skills). Logs decision details to story log.”

## Test plan

See the full in-game plan in [`assets/in-game-test-plan.md`](assets/in-game-test-plan.md).

## Docs enforcement (CI checks)

The `Verify docs updates` GitHub Actions workflow (`.github/workflows/verify-docs.yml`) enforces that every push/PR updates both `README.md` and `assets/in-game-test-plan.md`, or the check will fail. The rationale is to keep documentation and the in-game test plan in sync as behavior changes. To satisfy the check for no-op changes, add a minimal note or clarification to both files (and remove it later if needed).

Quick smoke checklist:

1. Put `simulation-mode.ts4script` into `Mods/SimulationMode/`.
2. Add `simulation-mode.txt` to `Mods/SimulationMode/`.
3. Enable script mods and restart.
4. In-game, run `simulation status` (should show loaded + version if provided).
5. Run `simulation true`, wait 3–5 seconds, then run `simulation status` again (confirm `tick_count` is > 0 and `daemon_running` is `True`).
6. Run `simulation debug` and confirm `clock_speed`/alarm info are printed.
7. Pause the game for 2 seconds and confirm it auto-unpauses (if `auto_unpause=true`).
8. Let a Sim dip into yellow motives and confirm the guardian pushes a self-care interaction.
