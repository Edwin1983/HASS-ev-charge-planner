# EV Charge Planner

Native Home Assistant custom integration for EV charging planning using electricity prices and PV forecasts.

**Current release: 1.1.1**

## What's new in 1.1.1

Version 1.1.1 is a maintenance release focused on validation and preparation for a future Home Assistant Core contribution.

The 1.1.0 native-entity changes described below remain the functional baseline.

## What's new in 1.1.0

Version 1.1.0 makes the planner settings native Home Assistant entities instead of requiring user-created `input_*` helpers.

Highlights:

- native departure **time** plus separate departure **day**;
- native sliders for energy needed, maximum grid price and minimum PV;
- maximum grid price is configured in **ct/kWh**;
- native planner mode and PV-current rounding selectors;
- native planner status, plan data, charging permission, desired current and desired phase outputs;
- native settings restore their values after a Home Assistant restart;
- compatibility fallbacks for existing installations using the previous `input_*` helpers;
- refined normal-mode and solar-only planning behavior;
- source-hour-aligned charging windows, with partial intervals only where required at the beginning or end;
- improved integration and regression test coverage.

The planner remains a decision layer only: Home Assistant automations are responsible for translating planner outputs into physical charger control.

## What it does

EV Charge Planner calculates **when and how much** an EV should charge before a configured departure time.

It combines:

- electricity price data;
- Solcast PV forecasts;
- required charging energy;
- a maximum grid-price limit in ct/kWh;
- a minimum usable PV-energy setting for solar-only planning;
- a configurable phase-switch budget;
- the configured maximum charging power.

**EV Charge Planner does not control the physical charger.** It does not switch a charger on/off, set charging current, change phases, or detect whether an EV is connected. Those actions remain Home Assistant automation responsibilities.

## Installation

### HACS

Add this repository as a custom HACS repository with category **Integration**.

After installation, restart Home Assistant. The integration is available under:

**Settings → Devices & services → Add integration → EV Charge Planner**

### Manual

Copy `custom_components/ev_planner/` to `/config/custom_components/ev_planner/`, restart Home Assistant, and add **EV Charge Planner** from the integration UI.

## Configuration

EV Charge Planner uses a config flow and an options flow.

During initial configuration you select only the external data sources:

| Setting | Entity type |
|---|---|
| Electricity prices | `sensor` |
| Solcast today | `sensor` |
| Solcast tomorrow | `sensor` |

The planner settings are provided as **native Home Assistant entities** by the integration. They are no longer required as existing `input_*` helpers during setup.

### Native planner settings

The integration provides these configurable entities:

| Setting | Native entity | Default |
|---|---|---:|
| Departure time | `time.ev_charge_planner_departure_time` | 23:59 |
| Departure day | `select.ev_charge_planner_departure_day` | Vandaag |
| Energy needed | `number.ev_charge_planner_energy_needed` | 10 kWh |
| Maximum grid price | `number.ev_charge_planner_maximum_grid_price` | 0 ct/kWh |
| Minimum PV for solar-only | `number.ev_charge_planner_minimum_pv_for_solar_only` | 0 kWh |
| Maximum phase switches | `number.ev_charge_planner_maximum_phase_switches` | 8 |
| Maximum charging power | `number.ev_charge_planner_maximum_charging_power` | 11.04 kW |
| Planner mode | `select.ev_charge_planner_planner_mode` | integration default |
| PV current rounding | `select.ev_charge_planner_pv_charging_current_rounding` | integration default |

Home Assistant may change entity IDs through the entity registry. Use the entity registry/UI when referencing these entities from automations.

The native entities restore their previous values after a restart. The departure time is a time-only entity; the separate departure-day entity determines whether that time applies to today or tomorrow. Existing installations can also use the previous `input_datetime`, `input_select` and `input_number` settings as compatibility fallbacks where applicable.

### Planner modes

**Normaal laden** uses the normal price/PV optimizer. It plans the required charging energy as cheaply as possible before departure. PV energy is treated as free, while grid charging is only used when the configured price limit permits it.

**Alleen zonneladen** uses only periods with usable PV production. The planner maximizes useful PV charging and does not create grid-only charging periods. The minimum-PV setting filters out small PV periods in this mode; it does not restrict normal price-based charging.

### PV-laadstroom afronden

In **Alleen zonneladen**, the planner derives a charging current from available PV power:

- **Naar beneden — geen netenergie** selects the highest valid current that does not exceed available PV power. If PV is below the minimum 6 A charging current, that period is not selected.
- **Naar boven — kleine netaanvulling toegestaan** selects the lowest valid current that reaches or exceeds available PV power. The difference is treated as paid grid energy for that period.

For example, with 2.1 kW available PV on 1 phase, rounding down selects 9 A (2.07 kW), while rounding up selects 10 A (2.30 kW).

The planner respects its electrical limits: 6–16 A integer current, 1-phase/3-phase operation, the configured maximum charging power, and the hard phase-switch safety cap.

## Entities

The integration provides planner settings and outputs as native Home Assistant entities.

### Planner control and status

- **Smart Charging switch** — enables/disables planner operation. It is not charger control.
- **State sensor** — reports the current planner state.
- **Data sensor** — exposes the complete current plan as state attributes.
- **Charging Allowed binary sensor** — read-only indication that the current planner window permits charging.
- **Desired charging current sensor** — reports the current from the active planner decision in amperes. It reports `0 A` when there is no active charging decision.
- **Desired phase sensor** — reports `1` or `3` for the active planner decision and `0` when there is no active charging decision.

The desired current, desired phase and charging-allowed entities are **planner outputs**. They do not directly change charger settings. Your Home Assistant automations can translate these outputs into charger control.

## Recommended architecture

```text
Home Assistant automation
        │
        ├── EV connected / planning trigger
        ├── update EV SOC / required energy
        └── ev_planner.create_plan
                    │
                    ▼
             ┌───────────────────┐
             │ EV Charge Planner │
             └─────────┬─────────┘
                       │
                planner outputs
                       │
        ┌──────────────┼────────────────┐
        ▼              ▼                ▼
    plan data     desired current  desired phase
        │              │                │
        └──────────────┼────────────────┘
                       ▼
              Your HA automation
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
        charger on/off      current / phase
```

This separation is intentional: the planner is the decision layer, while Home Assistant automations are the physical charger control layer.

## Services

### `ev_planner.update`

Runs one planner update cycle. The integration does not start its own periodic background loop; call this service from your own automation when appropriate.

### `ev_planner.create_plan`

Creates a new charging plan from the current inputs.

### `ev_planner.replan`

Clears the current plan and creates a new one.

### `ev_planner.clear_plan`

Clears the current plan.

### `ev_planner.status`

Returns the current runtime status and supports Home Assistant response data.

### `ev_planner.dashboard`

Returns current planner/dashboard data and supports Home Assistant response data.

## Planning behavior

The planner's objective is to schedule the required energy before departure while respecting the configured constraints.

- PV energy is treated as free.
- Grid charging is permitted only when the electricity price is within the configured maximum, except for the intentional small grid supplement allowed by PV rounding-up mode.
- In normal mode, the minimum-PV setting is ignored.
- In solar-only mode, only usable PV periods are planned.
- The first active interval may start at the current time and the final active interval may end at departure.
- Intermediate active intervals preserve source-hour boundaries.
- Separate active charging blocks may have idle gaps.
- Phase-switch counting is reset across idle/off windows.

## Electrical limits

The current planner uses:

- 230 V;
- 6–16 A integer charging current;
- 1-phase and 3-phase charging;
- maximum 11.04 kW by default;
- configurable maximum charging power;
- configurable phase-switch budget with a hard safety cap.

Typical reference powers are approximately:

- 1-phase 6 A: 1.38 kW;
- 1-phase 16 A: 3.68 kW;
- 3-phase 8 A: 5.52 kW;
- 3-phase 16 A: 11.04 kW.

## Planner output

The planning data sensor exposes the plan summary and individual decisions, including where available:

- required energy;
- planned energy;
- missing energy;
- free PV energy;
- paid grid energy;
- estimated cost;
- completeness;
- departure time;
- maximum price;
- charging-window count;
- phase-switch count;
- total charging minutes;
- individual charging decisions.

Individual decisions contain the source hour and the calculated charging settings, including charging current, number of phases and active charging interval.

## Data sources

EV Charge Planner currently uses data supplied by other Home Assistant integrations. The recommended setup uses:

- **Solcast** for hourly PV forecasts;
- an electricity-price integration such as **Zonneplan** for price data.

The integration does not hard-code those provider entities. You select the relevant Home Assistant sensors during configuration, so entity names can differ between installations.

## Troubleshooting

If the planner does not produce a plan:

1. Open **Settings → Devices & services → EV Charge Planner**.
2. Check the configured electricity-price and Solcast entities.
3. Check the native planner settings and make sure the departure day/time and required energy are valid.
4. Check the planner state and planning-data entities for the reported reason.
5. If you use automations, verify that they call `ev_planner.create_plan` or `ev_planner.replan` at the intended time.

For issues or feature requests, use the repository issue tracker.

## Development

Run the test suite locally with:

```bash
python -m pytest -q
python -m ruff check .
```

GitHub Actions also validates pytest/Ruff, Home Assistant Hassfest and HACS integration validation on pushes and pull requests.

## License

This project is licensed under the MIT License. See `LICENSE` for details.
