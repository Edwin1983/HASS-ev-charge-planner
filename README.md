# EV Charge Planner

Native Home Assistant custom integration for EV charging planning using electricity prices and PV forecasts.

**Current release line: 2.0.0**

## What's new in 2.0.0

Version 2.0.0 introduces a memory-safe production planner implementation while retaining the native Home Assistant entity model and decision-layer architecture introduced in 1.1.x.

Highlights:

- memory-safe dynamic programming with direct predecessor reconstruction;
- bounded phase-switch state space with a hard safety cap;
- native planner mode with immediate replanning when the mode changes;
- native planner settings and outputs remain available through Home Assistant entities;
- solar-only planning and PV-current rounding remain supported;
- compatibility fallbacks for existing installations using the previous `input_*` helpers;
- the planner remains a decision layer; an optional, separate charge control (disabled by default) can drive the charger, see [Charge control](#charge-control-optional).

Highlights:

- existing service calls without `config_entry_id` continue to work when exactly one EV Charge Planner configuration is loaded;
- `config_entry_id` can still be supplied explicitly, and is required when multiple EV Charge Planner configurations are loaded;
- native departure **time** plus separate departure **day**;
- native sliders for energy needed, maximum grid price and minimum PV;
- maximum grid price is configured in **ct/kWh**;
- native planner mode and PV-current rounding selectors;
- native planner status, plan data, charging permission, desired current and desired phase outputs;
- native settings restore their values after a Home Assistant restart;
- compatibility fallbacks for existing installations using the previous `input_*` helpers;
- refined normal-mode and solar-only planning behavior;
- source-hour-aligned charging windows, with partial intervals only where required at the beginning or end;
- improved integration and regression test coverage;
- support for Zonneplan quarter-hour electricity prices;
- legacy Zonneplan hourly price data remains supported;
- Solcast half-hourly and hourly forecast data are converted to interval energy correctly;
- Solcast PV energy is proportionally distributed across quarter-hour price slots so PV is not counted multiple times.

The planner remains a decision layer. By default, Home Assistant automations translate planner outputs into physical charger control. Alternatively you can enable the optional [charge control](#charge-control-optional).

## Dashboard

A dashboard can be built directly from the native planner entities. The example below shows the main planner settings, status and planning outputs in Home Assistant.

![EV Charge Planner dashboard](images/Dashboard.png)

The dashboard is an example only. Entity IDs may differ between installations, so use the entities created by your own EV Charge Planner config entry.

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

**By default EV Charge Planner does not control the physical charger.** The planner itself never switches a charger on/off, sets charging current or changes phases; those actions remain Home Assistant automation responsibilities unless you configure the optional [charge control](#charge-control-optional), which is a separate component that is disabled until you switch it on.

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
| Minimum energy needed | `number.ev_charge_planner_energy_needed` | 10 kWh |
| Maximum energy needed | `number.ev_charge_planner_maximum_energy_needed` | 0 kWh (off) |
| Price limit extra charging | `number.ev_charge_planner_price_limit_extra_charging` | 5 ct/kWh |
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

## Prices not yet known for tomorrow

Day-ahead prices are often published in the afternoon. A plan made before that only knows the hours up to the end of the known prices. The planner now remembers how far the prices reached; when more prices appear and the plan did not yet cover the departure time, it re-plans automatically (checked at most every 5 minutes). The plan attributes `prices_valid_until` and `prices_cover_departure` show what the plan was based on.

### Maximum pauses

The number entity *Maximum pauses* limits how often a plan may stop and start charging again. A pause is a gap between two charging periods; time before the first and after the last charging period does not count. `0` means one continuous charging block, `1` at most two blocks, and so on. The highest value (10) means no limit. Fewer pauses can cost a little more, and if the target cannot be reached within the limit the planner plans as much as possible. The plan attributes `pauses` and `max_pauses` show the result.

### Forecast prices

In the options you can set an optional *Forecast prices* sensor (default `sensor.energy_price_forecast_eu_nl_current_retail_price`). It must have a `raw_forecast` attribute with records `start`, `end` and `value` (EUR/kWh). Only slots that directly follow the final prices are used, so real prices are never replaced by a forecast. A fixed markup of 2 ct/kWh (`FORECAST_PRICE_MARKUP` in `const.py`) is added to forecast prices, never to final prices. Decisions based on a forecast carry `price_is_forecast: true`, and the plan attributes `forecast_prices_used` / `forecast_price_slots` tell whether a forecast was used. When the real prices are published the plan is recalculated automatically. Clear the field to switch the feature off.

## Charge control (optional)

The charge control is a separate component inside the integration that brings your charger to the state the planner wants (charging allowed, number of phases, charging current). It replaces the typical "charging control" automation, and it also compares what the planner expected to charge with what was actually charged.

It is **only available when you configure the charger entities** in the integration options, and even then it does nothing until you switch it on.

### Configuration

Open the integration options and fill in the charger fields. All fields are optional, but the charge control needs at least the **mode sensor** and the **current number**.

The first time you open the options, the charger fields are pre-filled with suggested values (`sensor.ev_charger_status`, `number.ev_charger_max_current`, `switch.ev_charger_charging`, `button.ev_charger_stop_charging`, the phase switch number with 32 = 1 phase and 0 = 3 phases). **Check them and adjust them to your charger before saving.** Saving the form makes the charge control available, but it stays off (and in dry-run) until you switch it on. The energy sensor is pre-filled with `sensor.ev_charger_session_total_charge`.

| Option | Meaning |
|---|---|
| Charger mode sensor | Sensor with the charger state (for example `connected_charging`). |
| States for connected / charging / startable | Comma separated lists of mode values. Defaults: `connected_requesting,connected_charging,connected_finished` / `connected_charging` / `connected_requesting,connected_finished`. |
| Charging current number | Number entity that sets the maximum current (A). |
| Start/stop switch | Switch that starts charging. Without one, the charger is assumed to start by itself once a current is set. |
| Stop button or switch | Pressed or turned off to stop charging. Defaults to turning off the start switch. |
| Phase switch number | Number entity that switches between 1 and 3 phases. Leave empty if your charger cannot switch phases. |
| Phase values | Values of the phase number meaning 1 phase (default 32) and 3 phases (default 0). Any other value is treated as "unknown" and is never switched. |
| Charger energy sensor | Total or session energy (kWh or Wh). Enables the planned-versus-actual feedback. |

### Switches

| Switch | Default | Meaning |
|---|---|---|
| Charge control active | off | Master switch. Off means the charger is left completely alone. |
| Charge control dry-run | **on** | Only logs what would be done. Run it in dry-run for a few sessions before going live. |
| Charge control energy feedback | on | Planned-versus-actual correction (needs the energy sensor). |

If you currently use a charging control automation, **disable it before turning the charge control live**, otherwise the two will fight each other.

### How it works

The charge control always takes the smallest safe step and re-reads the planner after every step:

1. charging not allowed: stop, then set the current to 0;
2. wrong phase: stop first (never switch phases under load), then switch phase, then wait for confirmation;
3. wrong current: set the current;
4. everything matches and the charger waits for a start: start.

If the planner changes while a (slow) phase switch is running, the new plan is used immediately, and charging is never started with a phase that no longer matches the plan. Protections: a minimum time between phase switches (120 s), a start cooldown (60 s) with at most 3 start attempts (then it stops and notifies you; it tries again when the car is reconnected), and retry with back-off plus a persistent notification when a command is not confirmed. When the planner is unavailable, the charger is left as it is. When the *Smart Charging* switch is off, the charge control releases the charger.

### Planned versus actual energy

A plan assumes ideal power. In reality a phase switch costs time, the car may limit the current, or the charger may start late. After **every charging block** (a run of consecutive planned slots) the cumulative planned energy is compared with the cumulative energy the charger actually delivered since the plan was made. If the difference is 0.3 kWh or more (in either direction), the plan is recalculated with the **actually remaining** energy.

- A *session* starts when the car is connected (or when you change the "energy needed" value) and ends when the car is disconnected. The remaining energy is always `target - delivered during the session`; the "energy needed" setting itself is never modified.
- Meter resets (session meters that fall back to 0) are handled.
- Blocks during which the car was disconnected are not counted as a deviation.
- At most one automatic re-plan per 5 minutes. If a re-plan fails, the previous plan stays active.
- When the target is reached, planning stops ("Doel bereikt").
- Session and meter state survive a Home Assistant restart.

The same comparison also runs **while a block is charging**: after a 3-minute start-up period, a deviation of 0.5 kWh by default (adjustable with the number *Charge progress threshold*, 0.1–3 kWh; confirmed by two consecutive checks) triggers an immediate re-plan of the remaining energy, at most once every 5 minutes. The charger can then keep charging instead of stopping at the end of the planned block. The comparison is made at the time of the last meter reading, so a slow sensor does not count as a shortfall.

The result is visible in the *Energy deviation (last block)* sensor (actual minus planned, in kWh) and its attributes (target, delivered, remaining, number of re-plans).

> The energy sensor measures energy at the charger, while the "energy needed" value is whatever you entered. If you enter battery energy, expect charging losses (often 5–10 %) to show up as a persistent shortfall.

## Services

### `ev_planner.update`

Runs one planner update cycle. The planner does not start its own periodic background loop; call this service from your own automation when appropriate. (When the optional charge control is configured, it refreshes the planner itself every 30 seconds.)

### `ev_planner.create_plan`

Creates a new charging plan from the current inputs.

For a single configured EV Charge Planner instance, `config_entry_id` is optional for backwards compatibility. When multiple instances are configured, specify the target `config_entry_id`.

### `ev_planner.replan`

Clears the current plan and creates a new one.

### Minimum and maximum energy

The planner always secures the **minimum** first, at the usual maximum price. If you also set a **maximum** (above the minimum), extra energy up to that maximum is added only from free PV or from slots at or below *Price limit extra charging* (negative prices count). Without such slots you simply get the minimum. With the charge control, planned-versus-actual checks and the progress check apply to the **minimum** only (extra charging is optional and never triggers corrections). Once the minimum is reached and nothing cheap is available, nothing more is planned and it is checked again every 5 minutes. When the maximum is reached (or on `clear_plan`) both numbers go back to 0. Entity ids may differ slightly if your installation named them differently.

### `ev_planner.clear_plan`

Also sets *Energy needed* back to 0 kWh and ends the current charging session. The same happens automatically when charging is complete (charge control with energy feedback).

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

## Zonneplan quarter-hour prices

The planner supports the current Zonneplan quarter-hour price format. Each price record can contain `start_date`, `end_date` and `price_tax_included.amount`. The amount is converted to €/kWh using Zonneplan's price representation.

### Negative prices and incomplete plans

Negative electricity prices are valid and are planned like any other price; they are the cheapest slots of the day.

If the required energy cannot be delivered (departure too close, too few slots at or below the maximum price, or the phase-switch budget is too tight), the planner does **not** discard the plan. It schedules the maximum energy that is achievable and chooses the cheapest way to do so. The result is marked incomplete: `complete` is false and `missing_energy_kwh` shows how much could not be planned. Only when no slot can be used at all (for example every slot is above the maximum price) is the plan empty.

Quarter-hour prices are treated as individual planning intervals. This means the planner can select some 15-minute periods within an hour while leaving other periods unused when their prices or planning conditions differ.

For backwards compatibility, the previous hourly Zonneplan format using `start_date` and `electricity_price` remains supported. The default price entity is now `sensor.zonneplan_current_quarter_hourly_electricity_tariff`, but the electricity-price entity remains configurable in the integration setup.

When quarter-hour prices are combined with Solcast forecasts, the planner distributes each Solcast forecast interval over the overlapping price intervals. This preserves the original forecast energy instead of counting the same PV forecast multiple times.

## Data sources

EV Charge Planner currently uses data supplied by other Home Assistant integrations. The recommended setup uses:

- **Solcast** for PV forecasts, supporting both half-hourly `detailedForecast` data and hourly `detailedHourly` data;
- an electricity-price integration such as **Zonneplan**, supporting both legacy hourly prices and the current quarter-hour price format.

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
