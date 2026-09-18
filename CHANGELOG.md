# Changelog

All notable changes to EV Charge Planner are documented here.

## [1.1.0-beta.3] - 2026-09-18

### Changed

- Updated the integration version to `1.1.0-beta.3` for the next beta release.
- Continued beta validation of the native EV Charge Planner integration.

### Validation

- Pytest: passed in the preceding validated beta cycle.
- Ruff: passed in the preceding validated beta cycle.
- Hassfest: passed.
- HACS validation: passed.

## [1.1.0-beta.2] - 2026-09-18

### Changed

- Refined solar-only planning so PV eligibility, PV rounding, and charging-energy targeting are handled consistently.
- Solar-only planning ignores `ev_kwh_nodig` as an optimization target and follows available PV hour by hour.
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
