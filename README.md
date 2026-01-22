# Simulation Mode Kernel Mod (v0.5.0, Build 80 — see VERSION.txt)

## What it is

This is a minimal Sims 4 script mod kernel that registers the `simulation` cheat-console command and runs a lightweight real-time watchdog while Simulation Mode is enabled. The watchdog can:

* Push real in-world self-care interactions (sleep/eat/toilet/shower) when household Sims are trending toward yellow/red motives.
* Block pregnancy unless explicitly allowed (to avoid naming dialogs during unattended play).
* Optionally auto-unpause if the game clock is paused.
* Toggle death on/off while Simulation Mode is enabled (reasserted periodically).

## What it is not (v0.5.0 non-goals)

* No full action/event logging; only lightweight story-log events for key actions.
* No cheating motive values or filling needs.
* No complex autonomy rewrites beyond light interaction pushes.
* No attempt to override global game options (aging, etc.).
* No attempt to handle every modal dialog in the game.

## Known recent issues / logging

* Skill notifications can occur from create-on-read; now logged as story event: `director_skill_stat_fallback_create_on_read`.
* New opt-in setting: `director_idle_override_allow_bypass_cooldown_once` (default false).
* `director_push` events now include `push_details` and no longer show unknown labels.

## Prereqs

* Python 3 is required to build the `.ts4script` archive.

## Build

```bash
python tools/build_ts4script.py
```

The build always outputs `dist/simulation-mode.ts4script`. Versioning is tracked in
`VERSION.txt` (synced from `src/simulation_mode/version.py` via `tools/sync_version_files.py`,
and optionally git tags), not in the filename. The artifact name is always
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
| Collect snapshot log | `simulation collect` |
| Force object scan | `simulation force_scan` |
| Trigger skill plan | `simulation skill_plan_now [SimFirstName]` |
| Trigger wants plan | `simulation wants_plan_now` |
| Trigger aspiration plan | `simulation aspiration_plan_now` |
| Trigger holiday plan | `simulation holiday_plan_now` |
| Probe holiday interactions | `simulation holiday_probe_now` |
| Popup probe (observability) | `simulation popup_probe` |

Notes:

* The Life Director nudges real skill-building interactions (no motive/skill cheating) when Sims are safe and idle.
* `death.toggle` is applied on enable and reasserted periodically while Simulation Mode is running.
* The mod only operates on active household Sims.

## Guardian escalation (opt-in)

Default behavior is unchanged unless `guardian_interrupt_running_noncritical=true` is set (now the default for new installs). When enabled, the guardian can interrupt long-running, noncritical interactions after repeated strikes to prevent them from blocking care pushes. Two thresholds are involved:

* `director_min_safe_motive`: unsafe gate that triggers Director → Guardian assist.
* `guardian_interrupt_noncritical_motive_threshold`: noncritical interruption gate (defaults to `-10` for new installs).

Recommended test values:

```
guardian_interrupt_running_noncritical=true
guardian_interrupt_noncritical_motive_threshold=-25
guardian_interrupt_noncritical_strikes=3
guardian_noncritical_cancel_cooldown_seconds=90
guardian_force_push_on_noncritical_interrupt=true
```

Expected story-log flow when escalation triggers: `guardian_noncritical_interrupt_waiting` → `guardian_noncritical_cancel` → `guardian_push` (or `guardian_push_failed`).

During the noncritical cancel cooldown window, guardian may attempt controlled forced pushes without repeated canceling. New settings:

* `guardian_noncritical_force_push_during_cancel_cooldown`: allow force-push attempts during the cancel cooldown window.
* `guardian_noncritical_force_push_cooldown_seconds`: minimum seconds between those force-push attempts.

### Test: infinite action while unsafe

1. Enable simulation.
2. Let a Sim start an infinite-ish action (singing/mirror/etc).
3. Wait until a motive drops below `director_min_safe_motive`.
4. Run `simulation collect`.
5. Confirm collect shows guardian interrupt thresholds and the story log includes guardian noncritical interrupt events.

## Wants/Aspirations/Holidays (Bedrock)

We now resolve Wants/Aspiration/Holiday objectives to INTERACTION tuning GUID64s and bridge
those to scanned lot affordances via `capabilities.by_aff_guid`. No keyword matching and no
heuristics are used; this is a direct GUID bridge.

Commands (operate on the active Sim):

* `simulation wants_plan_now`
* `simulation aspiration_plan_now`
* `simulation holiday_plan_now`
* `simulation holiday_probe_now`
* `simulation popup_probe`

These probes depend on capabilities.by_loot_guid containing real ACTION (loot action) GUIDs; run `simulation force_scan` after updating to rebuild the catalog/capabilities.

## Readiness diagnostics (wants/aspirations/holidays)

Wants, aspirations, and holiday processing remain disabled by default. The collect command now includes diagnostics for the interaction GUID bridge (resolved interaction GUID64s and availability on-lot), plus holiday service discovery hints.

## Testing workflow (for development)

Expected artifacts:

* `simulation-mode-collect.log`
* `simulation-mode-story.log`
* `simulation-mode-object-catalog.jsonl`
* `simulation-mode-capabilities.json`

### What `skill_plan_now` does

* Chooses a non-maxed skill for the selected active household Sim.
* Selects a candidate affordance from the catalog that should grant skill gain.
* Pushes the interaction.
* Verifies the skill gain and updates `SimulationMode/simulation-mode-verified-gain.json`.
* To reset verified actions, delete the verified-gain file while the game is closed.

## Test plan

See the full in-game plan in [`assets/in-game-test-plan.md`](assets/in-game-test-plan.md).

## Docs enforcement (CI checks)

The `Verify docs updates` GitHub Actions workflow (`.github/workflows/verify-docs.yml`) enforces that every push/PR updates both `README.md` and `assets/in-game-test-plan.md`, or the check will fail. The rationale is to keep documentation and the in-game test plan in sync as behavior changes. To satisfy the check for no-op changes, add a minimal note or clarification to both files (and remove it later if needed).

Quick smoke checklist:

1. Put `simulation-mode.ts4script` into `Mods/SimulationMode/`.
2. Add `simulation-mode.txt` to `Mods/SimulationMode/`.
3. Enable script mods and restart.
4. In-game, run `simulation collect` (should emit a snapshot log).
5. Run `simulation true`, wait 3–5 seconds, then run `simulation collect` again (confirm `tick_count` increases).
6. Pause the game for 2 seconds and confirm it auto-unpauses (if `auto_unpause=true`).
7. Let a Sim dip into yellow motives and confirm the guardian pushes a self-care interaction.
