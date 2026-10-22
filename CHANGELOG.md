# Changelog

All notable changes to EV Charge Planner are documented here.

## [Unreleased]

### Fixed

- **Departure date on automatic re-plans**: with departure day "Tomorrow", an automatic re-plan (energy feedback, progress check, new prices) on the day of departure moved the departure to the day after. Automatic re-plans now keep the original departure date as long as the departure time/day settings are unchanged and the departure has not passed. A re-plan by the user (service or button) still uses the current settings. The kept date is stored and survives a Home Assistant restart.

### Added

- **Minimum and maximum energy needed**: the *Energy needed* number is now the **minimum** (renamed "Minimum energy needed"; entity id unchanged). New numbers *Maximum energy needed* (kWh, 0 = off) and *Price limit extra charging* (ct/kWh, default 5). The planner first secures the minimum at the usual price limit; energy up to the maximum is only added from free PV or slots at or below the extra-charging price limit (negative prices count). If the minimum is already reached and no such slots exist, nothing is planned and it is retried every 5 minutes. The charge control checks against the **minimum**: the planned-versus-actual comparison and the in-block progress check only look at the first (minimum − already delivered) kWh of the plan, so deviations in extra, optional charging never trigger corrections. Extra charging simply continues according to the plan until the maximum is reached; the session ends, and both numbers are reset, when the maximum is reached or *clear_plan* is called both numbers are set back to 0.
- **Adjustable forecast markup**: new number entity *Forecast price markup* (ct/kWh, default 2.0, range 0–20, step 0.1) replaces the fixed 2 ct added to forecast prices. Final prices never get a markup.
- **Fallback to forecast prices during an outage**: if the actual price sensor cannot be read (e.g. "Forecast attribuut ontbreekt"), the plan is now made entirely from the forecast sensor (with the markup) instead of failing. The forecast sensor's `raw_today`, `raw_tomorrow` and `raw_forecast` lists are combined (duplicates removed), so the rest of today is covered too. As soon as the real prices are back, the plan is refreshed automatically. Without a forecast sensor the old behaviour (no plan) remains.
- **Energy needed resets to 0 kWh**: the *Energy needed* number (minimum is now 0) is set back to 0 kWh when the charge control sees the target reached, and by the `clear_plan` action. The charging session is ended too, so entering the same value again starts a new one. (With energy feedback switched off there is no "target reached" detection; `clear_plan` still resets.)
- **Optional charge control** (`core/charge_control.py`, `charge_manager.py`): a generic, entity-based regulator that brings the charger to the state the planner wants (allowed, phases, current) in the smallest safe steps. It never switches phases under load, re-reads the planner after every step, protects the relay (minimum time between phase switches, start cooldown and attempt limit) and reports problems with persistent notifications. Disabled until configured and switched on; ships with a dry-run mode (on by default).
- **Planned versus actual energy feedback** (`core/energy_feedback.py`): after every charging block the cumulative planned energy is compared with the energy the charger actually delivered; a deviation of 0.3 kWh or more re-plans with the actually remaining energy. Handles meter resets, disconnected cars and restarts; the "energy needed" setting is never modified.
- New entities when the charger is configured: *Charge control* and *Energy deviation (last block)* sensors; *Charge control active*, *dry-run* and *energy feedback* switches.
- Charger fields in the integration options (mode sensor, current, start/stop, phase switch and values, energy sensor), pre-filled the first time with suggested values.
- Controller hooks: `energy_override_kwh`, `get_desired_charge()`, `get_plan_decisions()`, `get_entity_energy_needed()` and `replan_keep_previous()`.
- **Progress check during charging**: besides the check after each charging block, the charge control now compares planned and delivered energy while a block is running (after a 3-minute start-up period, at a deviation confirmed twice, default 0.5 kWh, adjustable via the new number entity "Charge progress threshold" (0.1–3 kWh), at most every 5 minutes). If the charger is behind or ahead of schedule, the plan for the remaining energy is recalculated immediately, so a block can be extended instead of the charger stopping and having to restart later. The sensor lag is compensated by comparing at the time of the last measurement.
- Charge control: an extra round is now run at every change in the plan (start/end of a charging block) instead of waiting up to 30 s; minimum time between phase switches lowered from 120 s to 30 s and the start retry cooldown raised from 60 s to 120 s, so a slow charger is not restarted repeatedly.
- **Maximum number of pauses** (new number entity *Maximum pauses*, 0–10, default 10 = no limit): limits how many times the plan may stop and resume charging, so the charger is not switched on and off all the time (0 = one continuous block, 1 = at most two blocks). A pause is a gap between two charging periods; time before the first and after the last charging period does not count. The planner (including the fast planner) optimises within the limit; if the target cannot be met within it, the best feasible partial plan is made. Plan attributes `pauses` and `max_pauses`.
- **Forecast prices for hours without a final price** (optional entity, default `sensor.energy_price_forecast_eu_nl_current_retail_price`, attribute `raw_forecast`): hours after the end of the final prices are filled with forecast prices, flagged `price_is_forecast` per decision. A markup of 0.02 EUR/kWh (`FORECAST_PRICE_MARKUP` in `const.py`) is added to forecast prices only. Final prices are never overwritten. As soon as the real prices arrive the plan is recalculated. New plan attributes `forecast_prices_used` and `forecast_price_slots`. Clear the option field to disable.
- **Plan refresh when new prices arrive**: if the day-ahead prices are published after a plan was made and the plan did not yet reach the departure time, the plan is recalculated (checked at most every 5 minutes, previous plan kept on failure). New plan attributes `prices_valid_until` and `prices_cover_departure`.

### Performance

- Added `core/planner_fast.py`: a dense numpy implementation of the planner DP on the energy lattice. Same optimum as the existing DP (verified differentially), roughly 30x faster and ~3x less memory on 96-slot / 57 kWh workloads.
- The original DP remains as automatic fallback (numpy missing, infeasible target, non-lattice slot durations, failed self-check).
- Planner memory diagnostics are logged at debug level instead of warning.

### Fixed

- **Negative prices are now supported.** `PriceReader` no longer drops records with a negative price and no longer forces a negative current price to 0. Negative-price slots are the cheapest slots and are now planned like any other slot.
- **Infeasible targets now produce a partial plan instead of no plan.** If the target cannot be met (departure too close, too few slots below the maximum price, phase-switch budget), the planner schedules the maximum energy that is achievable (all slots at or below the maximum price, or with free PV), then picks the cheapest such plan. The plan is reported as incomplete (`complete=False`, `missing_energy_kwh`). Previously an infeasible target selected nothing at all, because states that could no longer reach the target were pruned before the final selection.
- Plan selection no longer prefers a more expensive plan that overshoots the target by a few micro-kWh (within tolerance) over a cheaper plan that hits the target exactly.
- The "cannot be planned completely" warning printed an internal integer key as kWh; it now prints kWh.
- Planner runs triggered from different services are now serialized with a lock.
- `create_plan` no longer raises when the price or Solcast sensor has no forecast data; it publishes "Geen prijsdata" / "Geen PV-data" like the other no-data paths.
- All platforms now report the same device name ("EV Charge Planner").
- The maximum-phase-switches number is limited to 8, matching the planner's internal hard cap.
- Added missing `data_description` helper texts to the en/nl translations.

### Cleanup

- Removed ~200 lines of commented-out debug logging from `core/prices.py` (no code change).

### Tests

- Added `tests/test_planner_fast.py` (fast DP vs. reference DP, including negative prices).
- Added `tests/test_controller_data_errors.py`, `test_charge_control.py`, `test_energy_feedback.py`, `test_charge_manager.py`, `test_controller_charge_hooks.py` and `test_charge_translations.py`.

## [2.0.0] - 2026-10-01

### Added

- Native planner mode changes now trigger an immediate replan.
- Production planning uses direct predecessor tracking for deterministic reconstruction.

### Changed

- Reworked the production dynamic-programming planner to avoid the previous checkpoint reconstruction approach.
- Added a hard safety cap of 8 phase switches to bound the planner state space.
- Preserved native Home Assistant settings, solar-only planning, PV-current rounding and charger-control separation.
- Kept compatibility fallbacks for existing `input_*` helper configurations.

### Removed

- Obsolete planner lint exceptions and unused planner bindings.

### Validation

- Pytest: passed.
- Ruff: passed.
- Hassfest: passed on the validated release baseline.
- HACS validation: passed on the validated release baseline.


## [1.1.5-beta.3] - 2026-09-27

### Changed

- Continued performance optimization of the planner's dynamic-programming scheduler.
- Kept the current stable performance implementation after evaluating additional pruning and dictionary-lookup experiments.
- Preserved the existing planner behavior and electrical constraints while improving scheduling runtime.

### Performance

- Current validated benchmark: approximately 5.3s median for the 57 kWh / 60 quarter-hour planning workload.
- The current version is approximately 29% faster than the historical pre-optimization baseline in the same benchmark workflow.

### Validation

- Pytest: passed.
- Ruff: passed.
- Performance benchmark: passed.

## [1.1.5-beta.2] - 2026-09-27

### Changed

- Optimized the planner finish-current lookup to avoid repeated linear current searches during scheduling.
- Added a repeatable performance benchmark for the 57 kWh / 60 quarter-hour planning workload.
- Added a same-run baseline comparison for the performance benchmark.

### Performance

- Baseline median planner runtime: 12.792s.
- Optimized median planner runtime: 11.692s.
- Median runtime reduction: 8.6%.

### Validation

- Pytest: passed.
- Ruff: passed.
- Performance benchmark: passed.

## [1.1.1] - 2026-09-19

### Changed

- Bumped the integration version for the 1.1.1 maintenance release.
- Updated release documentation and metadata to keep the repository aligned with the published 1.1.x release line.

### Validation

- Pytest: passed.
- Ruff: passed.
- Hassfest: passed.
- HACS validation: passed.

## [1.1.0] - 2026-09-18

### Added

- Native Home Assistant entities for planner settings:
  - departure time;
  - departure day;
  - energy needed;
  - maximum grid price;
  - minimum PV for solar-only charging;
  - maximum phase switches;
  - maximum charging power.
- Native planner mode and PV charging-current rounding selects.
- Native planner output entities for state, planning data, charging permission, desired current and desired phase.
- Restore support for native planner settings, with compatibility fallbacks for the previous input-helper configuration.

### Changed

- The config flow now configures only external electricity-price and Solcast entities; planner settings are managed as native entities by the integration.
- Refined normal charging so `min_pv_kwh` is ignored outside solar-only mode.
- Preserved full source-hour charging windows except where the first or last active hour must be partial.
- Removed arbitrary short charging windows inside a source hour.
- Corrected solar/PV energy accounting for partial active hours.
- Improved integration and regression test coverage for normal and solar-only planning.

### Validation

- Pytest: passed.
- Ruff: passed.
- Hassfest: passed.
- HACS validation: passed.

## [1.1.0-beta.4] - 2026-09-18

### Changed

- Refined normal charging behavior so min_pv_kwh is ignored outside solar-only mode.
- Preserved full source-hour charging windows except where the first or last active hour must be partial.
- Removed the short-window behavior that could create arbitrary charging intervals inside a source hour.
- Corrected solar/PV energy accounting for partial active hours.
- Added and aligned regression coverage for normal-mode minimum-PV handling and solar charging behavior.

### Validation

- Pytest: passed.
- Ruff: passed.
- Hassfest: passed.
- HACS validation: passed.

## [1.1.0-beta.3] - 2026-09-18

### Changed

- Updated the integration version to 1.1.0-beta.3 for the next beta release.
- Continued beta validation of the native EV Charge Planner integration.

### Validation

- Pytest: passed in the preceding validated beta cycle.
- Ruff: passed in the preceding validated beta cycle.
- Hassfest: passed.
- HACS validation: passed.

## [1.1.0-beta.2] - 2026-09-18

### Changed

- Refined solar-only planning so PV eligibility, PV rounding, and charging-energy targeting are handled consistently.
- Solar-only planning ignores ev_kwh_nodig as an optimization target and follows available PV hour by hour.
- Added and aligned functional coverage for PV rounding down/up, minimum PV filtering, phase switching, and solar-only energy accounting.
- Made integration planning tests safe across midnight when the configured departure day is tomorrow.
- Updated release metadata and repository links for the public repository.

### Validation

- Pytest: all tests passed.
- Ruff: passed.
- Hassfest: passed.
- HACS validation: passed.

## [1.1.0-beta.1]

Initial beta release of the native EV Charge Planner integration.
