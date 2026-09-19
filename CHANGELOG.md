# Changelog

All notable changes to EV Charge Planner are documented here.

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
