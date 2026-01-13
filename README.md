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

All commands are exposed under `simulation` (and the alias `simulation_mode`).

### Console command reference

| Command | What it does |
| --- | --- |
| `simulation status` | Show enablement state, daemon status, and tick counters. |
| `simulation true` | Enable Simulation Mode and start the daemon. |
| `simulation false` | Disable Simulation Mode and stop the daemon. |
| `simulation help` | Print usage plus available settings keys. |
| `simulation reload` | Reload `simulation-mode.txt` from disk and apply changes. |
| `simulation set <key> <value>` | Set a runtime setting (non-persistent). |
| `simulation set tick 1..120` | Set the watchdog tick interval in seconds. |
| `simulation tick <1..120>` | Shorthand for `simulation set tick ...`. |
| `simulation allow_pregnancy <true|false>` | Shorthand for `simulation set allow_pregnancy ...`. |
| `simulation auto_unpause <true|false>` | Shorthand for `simulation set auto_unpause ...`. |
| `simulation allow_death <true|false>` | Shorthand for `simulation set allow_death ...`. |
| `simulation debug` | Print daemon timing, alarm, and auto-unpause diagnostics. |
| `simulation director` | Show Life Director configuration, last run info, and motive snapshot. |
| `simulation director_gate` | Print green-gate evaluation (safe-to-push check). |
| `simulation director_now` | Force a Life Director run and print the last actions. |
| `simulation director_why` | Dump the most recent Life Director debug lines. |
| `simulation director_push <skill_key>` | Push a skill interaction on the active Sim. |
| `simulation director_takeover <skill_key>` | Cancel current interactions, then push a skill. |
| `simulation guardian_now [force]` | Force a guardian self-care push for the active Sim. |
| `simulation want_now` | Force the want resolver to push an active want for the active Sim. |
| `simulation configpath` | Print the resolved `simulation-mode.txt` path and existence. |
| `simulation dump_log` | Write a `simulation-mode.log` snapshot to disk. |
| `simulation probe_all` | Run all probe diagnostics and report to the probe log. |
| `simulation probe_wants` | Dump active wants to the probe log. |
| `simulation probe_want <index>` | Inspect a specific want slot by index. |
| `simulation probe_career` | Inspect career tuning and interactions in the probe log. |
| `simulation probe_aspiration` | Inspect aspiration tuning and interactions in the probe log. |

### Settings keys for `simulation set`

`auto_unpause`, `allow_death`, `allow_pregnancy`, `tick`, `guardian_enabled`,
`guardian_check_seconds`, `guardian_min_motive`, `guardian_red_motive`,
`guardian_per_sim_cooldown_seconds`, `guardian_max_pushes_per_sim_per_hour`,
`director_enabled`, `director_check_seconds`, `director_min_safe_motive`,
`director_green_motive_percent`, `director_green_min_commodities`,
`director_allow_social_goals`, `director_allow_social_wants`,
`director_use_guardian_when_low`, `director_per_sim_cooldown_seconds`,
`director_max_pushes_per_sim_per_hour`, `director_prefer_career_skills`,
`director_fallback_to_started_skills`, `director_skill_allow_list`,
`director_skill_block_list`, `integrate_better_autonomy_trait`,
`better_autonomy_trait_id`.

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

See the full in-game plan in [`assets/in-game-test-plan.md`](assets/in-game-test-plan.md).

Quick smoke checklist:

1. Put `simulation-mode.ts4script` into `Mods/SimulationMode/`.
2. Add `simulation-mode.txt` to `Mods/SimulationMode/`.
3. Enable script mods and restart.
4. In-game, run `simulation status` (should show loaded + version if provided).
5. Run `simulation true`, wait 3–5 seconds, then run `simulation status` again (confirm `tick_count` is > 0 and `daemon_running` is `True`).
6. Run `simulation debug` and confirm `clock_speed`/alarm info are printed.
7. Pause the game for 2 seconds and confirm it auto-unpauses (if `auto_unpause=true`).
8. Let a Sim dip into yellow motives and confirm the guardian pushes a self-care interaction.
