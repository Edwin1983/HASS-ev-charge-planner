# EV Charge Planner v3.0.0

Compared with **v2.1.0**. This release adds an optional, entity-based **charge control**, a **minimum/maximum energy** model, **forecast prices** for hours without a final price, and several planner controls. Everything new is opt-in; with the defaults the planner behaves as in 2.1.0.

## Highlights

- **Charge control (optional).** A generic regulator brings your charger to the state the planner wants (allowed, phases, current) in the smallest safe steps. It never switches phases under load, protects the relay (minimum time between phase switches, start cooldown, attempt limit) and reports problems with persistent notifications. Disabled until you configure and enable it, and it ships in **dry-run mode** (only logs what it would do).
- **Planned versus actual energy.** After every charging block, and also *while* a block is running, the delivered energy is compared with the plan. If you are behind or ahead of schedule the plan for the remaining energy is recalculated, so a block can be extended instead of the charger stopping early. The deviation threshold is configurable.
- **Minimum and maximum energy.** The planner secures the **minimum** first; energy up to the **maximum** is only added from free PV or slots at or below a configurable price limit (negative prices count). The charge control checks against the minimum only.
- **Forecast prices.** If the price for upcoming hours is not published yet (or the price sensor is down), an optional forecast sensor is used, with an adjustable markup. Final prices always take precedence.
- **Maximum number of pauses** between charging blocks, so the charger does not switch on and off all the time.

## Added

- Optional charge control: `core/charge_control.py`, `charge_manager.py`. New entities (only when a charger is configured): *Charge control* and *Energy deviation (last block)* sensors; *Charge control active*, *dry-run* and *energy feedback* switches. Charger fields in the integration options (mode sensor, current, start/stop, phase switch and values, energy sensor) with suggested values.
- Planned-versus-actual feedback (`core/energy_feedback.py`) with in-block progress check and a boundary check at every plan change.
- New number entities: *Maximum energy needed* (kWh, 0 = off), *Price limit extra charging* (ct/kWh, default 5), *Maximum pauses* (0–10, 10 = unlimited), *Forecast price markup* (ct/kWh, default 2.0), *Charge progress threshold* (kWh, default 0.5).
- Optional forecast price entity (default `sensor.energy_price_forecast_eu_nl_current_retail_price`); slots are flagged as forecast in the plan data. The plan is refreshed automatically when the final prices arrive. If the final price sensor cannot be read, the plan is built entirely from forecast prices.
- Fast planner supports the pause limit; controller hooks (`get_desired_charge()`, `get_plan_decisions()`, `replan_keep_previous()`, …) for the charge control.
- 100+ new tests (charge control, feedback, forecast prices, pauses, min/max energy, price horizon).

## Changed

- `number.ev_charge_planner_energy_needed` is now the **minimum** energy (label "Minimum energy needed"; entity id unchanged; the minimum value is now 0).
- `ev_planner.clear_plan`, and a completed charge (maximum reached), set the energy-needed numbers back to 0 kWh.
- Suggested charger entity names in the options form are generic (`sensor.ev_charger_status`, …); saved options are unaffected.

## Fixed

- Automatic re-plans keep the **original departure date** (with departure day "Tomorrow" they used to move it one day further on the day of departure). A re-plan you trigger yourself uses your current settings. The kept date survives a restart.
- Plans refresh when day-ahead prices appear after a plan was made that did not yet reach the departure time.

## Upgrade notes

- No action needed if you do not use the new features. Existing config entries keep working; the new numbers appear automatically.
- If your automation fills *Energy needed* (e.g. from the car's state of charge), you can now also fill *Maximum energy needed* (entity id is derived from the name; check it in your installation). Leave it at 0 to disable extra charging.
- The charge control is off and in dry-run by default. To let it act on the charger: enable *Charge control active*, disable *dry-run*, and make sure *Smart charging* is on.
- `clear_plan` now also resets the energy numbers; if your automation calls `clear_plan` first, set the numbers *after* it (as in the example automation).

## Validation

- The runnable test modules pass in a stubbed environment (about 200 tests, including the fast-versus-reference planner checks).
- **Not verified in a real Home Assistant:** config/options flow, entity registration and state restore, and the live behaviour of the charge control on real hardware. `hassfest` and `ruff` were not run. Please check these before tagging the release.
