# Changelog

All notable changes to EV Charge Planner are documented here.

## [Unreleased]

### Performance

- Added `core/planner_fast.py`: a dense numpy implementation of the planner DP on the energy lattice. Same optimum as the existing DP (verified differentially), roughly 30x faster and ~3x less memory on 96-slot / 57 kWh workloads.
- The original DP remains as automatic fallback (numpy missing, infeasible target, non-lattice slot durations, failed self-check).
- Planner memory diagnostics are logged at debug level instead of warning.

### Fixed

- Planner runs triggered from different services are now serialized with a lock.
- `create_plan` no longer raises when the price or Solcast sensor has no forecast data; it publishes "Geen prijsdata" / "Geen PV-data" like the other no-data paths.
- All platforms now report the same device name ("EV Charge Planner").
- The maximum-phase-switches number is limited to 8, matching the planner's internal hard cap.
- Added missing `data_description` helper texts to the en/nl translations.

### Cleanup

- Removed ~200 lines of commented-out debug logging from `core/prices.py` (no code change).

### Tests

- Added `tests/test_planner_fast.py` (fast DP vs. reference DP, including negative prices).
- Added `tests/test_controller_data_errors.py`.

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
