# Simulation Mode — In-Game Test Plan

## Scope

This plan validates that the Simulation Mode kernel loads, responds to console commands, and
executes guardian/director behavior safely during live gameplay.

## Preconditions

1. Build or download `simulation-mode.ts4script`.
2. Ensure `assets/simulation-mode.txt` is copied to `Mods/SimulationMode/simulation-mode.txt`.
3. Enable Script Mods + Custom Content in game options and restart The Sims 4.
4. Use a test household with at least one adult Sim and a safe lot (no active events).

## Baseline load & status

1. Open the cheat console (`Ctrl+Shift+C`) and run `simulation status`.
   * **Expected:** `enabled=False` (unless you already enabled), daemon status shown, version if provided.
2. Run `simulation configpath`.
   * **Expected:** `config_path=.../Mods/SimulationMode/simulation-mode.txt` and `exists=True`.
3. Run `simulation help`.
   * **Expected:** Usage list prints without errors.

## Enablement & watchdog

1. Run `simulation true`.
   * **Expected:** status shows `enabled=True`, daemon running, and a success message.
2. Wait 5 seconds and run `simulation status` again.
   * **Expected:** `tick_count` increased and `daemon_running=True`.
3. Run `simulation debug`.
   * **Expected:** includes `clock_speed`, `last_alarm_variant`, and tick/auto-unpause diagnostics.

## Auto-unpause (if enabled)

1. Pause the game for ~2 seconds.
2. If `auto_unpause=true`, verify it unpauses on its own.
   * **If it fails:** confirm daemon is running and `tick_count` is increasing.

## Guardian self-care

1. Let an active Sim’s motive drop into yellow (or set `guardian_min_motive` higher for faster repro).
2. Wait for guardian cycle.
   * **Expected:** a self-care interaction is pushed (sleep/eat/toilet/shower).
3. Run `simulation guardian_now`.
   * **Expected:** `guardian_now force=False pushed=True` plus detail message.
4. Run `simulation guardian_now force`.
   * **Expected:** bypasses cooldown and pushes an action (or reports why it could not).

## Life Director

1. Run `simulation director` and confirm config and last-run fields print.
2. Run `simulation director_gate`.
   * **Expected:** reports `green_gate_pass` and motive snapshot for active Sim.
3. Run `simulation director_now`.
   * **Expected:** `last_director_actions` prints and an interaction may be queued.
4. Run `simulation director_why`.
   * **Expected:** recent director debug lines or an empty set message.
5. Optional: run `simulation director_push <skill_key>` on a known skill.
   * **Expected:** success/failure message plus last action/debug.
6. Optional: run `simulation director_takeover <skill_key>` to cancel current interactions and push.

## Settings reload

1. Edit `simulation-mode.txt` (e.g., set `tick_seconds=2`).
2. Run `simulation reload`.
   * **Expected:** daemon applies changes; `simulation status` shows new tick behavior.

## Safety toggles

1. Run `simulation allow_death true`, then `simulation allow_death false`.
   * **Expected:** death toggle reasserts without errors.
2. Run `simulation allow_pregnancy false`.
   * **Expected:** pregnancy block patch applied without errors.

## Probe & log output

1. Run `simulation probe_all`.
   * **Expected:** console prompts you to run `simulation dump_log` when complete.
2. Run `simulation dump_log`.
   * **Expected:** `simulation-mode.log` is written to disk.
3. Run `simulation probe_wants`, `simulation probe_want 0`, `simulation probe_career`,
   and `simulation probe_aspiration`.
   * **Expected:** each writes diagnostics to the probe log (`simulation-mode-probe.log`).

## Shutdown

1. Run `simulation false`.
   * **Expected:** daemon stops and `enabled=False`.
2. Run `simulation status` one last time to confirm idle state.
