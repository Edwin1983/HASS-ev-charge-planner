# Home Assistant Core preparation audit

Date: 2026-09-20

This branch prepares the current EV Charge Planner custom integration for a future
Home Assistant Core contribution. It intentionally does **not** move the integration
into `homeassistant/components` yet and does not change the planning algorithm.

## Current assessment

### Already in good shape

- UI config flow and options flow are present.
- The integration uses a single config entry.
- Platform setup/unload is implemented through config entries.
- Services are registered from `async_setup_entry` and removed on unload.
- The physical charger remains outside the integration; the planner is a decision layer.
- Native entities are used for planner settings and outputs.
- The integration has integration tests plus planner/core tests.
- A brand directory is already present.
- No third-party Python requirements are declared.
- The config flow now provides `data_description` text for its external entity selectors.

### Current blockers before a Core PR

1. **Repository layout**
   - The current repository is a HACS custom-integration repository.
   - Home Assistant Core expects the integration under
     `homeassistant/components/ev_planner/` and its tests under
     `tests/components/ev_planner/`.
   - The HACS-specific repository files must not be copied into Core.

2. **Documentation URL**
   - The current manifest points to this GitHub repository.
   - A Core manifest must point to the Home Assistant integration documentation
     page once that documentation exists.

3. **Manifest metadata**
   - The custom manifest contains a custom-integration `version`.
   - Core integrations do not use the custom-integration version field.
   - `quality_scale` and the final Core `codeowners` value need to be established
     as part of the Core contribution.

4. **Custom-integration compatibility layer**
   - The controller still contains fallbacks to legacy `input_*` helper entities.
   - These are useful for HACS upgrades but should be separated from the Core
     implementation so the official integration has a clean configuration model.
   - Existing HACS users must not lose compatibility as a side effect of the Core work.

5. **Home Assistant adapter boundary**
   - `core/homeassistant.py` is currently a custom read-only wrapper around HA.
   - Before Core submission, the boundary should be reviewed module-by-module:
     pure planning code should remain independent, while HA entity/state access
     should use current Core APIs directly at the integration boundary where that
     improves typing, testability, and maintainability.

6. **Typing and Core style**
   - The HA-facing modules are partly typed, while substantial controller/planner
     code still uses broad/untyped arguments.
   - This is not a reason to rewrite the optimizer now, but Core preparation should
     progressively add precise types to the integration boundary and public
     controller interfaces.

7. **Test placement and coverage**
   - The current tests run correctly as a custom integration.
   - A Core contribution needs the standard Core test layout and Core fixtures.
   - Config-flow coverage must include the happy path, single-entry restriction,
     options flow, and recovery/error paths as applicable.
   - Core integration modules should ultimately meet the current coverage and
     quality-scale requirements.

8. **Services**
   - The six services are implemented and documented.
   - Before a Core PR, response-returning services (`status` and `dashboard`)
     should be reviewed against current Core service-response conventions and
     covered by focused tests.

9. **Documentation**
   - The current README is written for HACS installation.
   - The future Core contribution needs Home Assistant documentation covering
     setup, configuration parameters, entities, actions, troubleshooting and
     example usage. HACS installation instructions belong only in the custom repo.

## Important architectural decision

The planner algorithm should **not** be rewritten as part of the first Core migration.

The target architecture is:

```
Home Assistant Core integration
        |
        +-- config flow / options
        +-- native entities
        +-- services/actions
        +-- HA state adapters
        |
        v
EV Planner controller / domain logic
        |
        +-- prices
        +-- PV forecast
        +-- planner
        +-- scheduler
        +-- status
```

The planner remains advisory. It does not switch the physical charger, change
charger current, change phases, or detect vehicle connection.

## Next migration stages

1. Create a Core-facing branch/package layout without changing planner behaviour.
2. Split HACS compatibility from the Core-facing configuration model.
3. Migrate tests to Core conventions and increase HA-facing coverage.
4. Add Core-quality documentation and final manifest metadata.
5. Run Core's current validation tooling (Hassfest, Ruff, mypy/tests as required).
6. Open a draft PR against `home-assistant/core`.
7. Keep the HACS repository usable until the Core contribution is accepted.

## What this branch deliberately does not do

- No charger-control logic is added.
- No planner/scheduler algorithm is changed.
- No existing HACS installation path is removed.
- No claim is made that the integration currently satisfies all Core quality-scale
  requirements.
