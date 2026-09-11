# EV Planner

Native Home Assistant custom integration for EV charging planning using electricity prices and PV forecasts.

## What it does

EV Planner calculates **when and how much** an EV should charge before a configured departure time.

It combines:

- electricity price data;
- Solcast PV forecasts;
- required charging energy;
- a maximum grid-price limit;
- optional solar-only operation;
- a configurable phase-switch budget;
- the electrical limits configured for the planner.

**EV Planner does not control the physical charger.** It does not switch a charger on/off, set charging current, change phases, or detect whether an EV is connected. Those actions remain Home Assistant automation responsibilities.

## Installation

### HACS

Add this GitHub repository as a custom HACS repository with category **Integration**.

After installation, restart Home Assistant. The integration will be available under:

**Settings → Devices & services → Add integration → EV Planner**

### Manual

Copy:

```text
custom_components/ev_planner/
```

to:

```text
/config/custom_components/ev_planner/
```

Restart Home Assistant and add **EV Planner** from the integration UI.

## Configuration

EV Planner uses a config flow and options flow. The following existing Home Assistant entities are selected during configuration:

| Setting | Entity type |
|---|---|
| Departure time | `input_datetime` |
| Departure day | `input_select` |
| Energy needed | `input_number` |
| Maximum grid price | `input_number` |
| Minimum usable PV | `input_number` |
| Maximum phase switches | `input_number` |
| Planner mode | `input_select` |
| Solar-only charging | `input_boolean` |
| Electricity prices | `sensor` |
| Solcast today | `sensor` |
| Solcast tomorrow | `sensor` |
| Maximum charging power | numeric setting |

The default entity IDs are compatible with the original EV Planner/Pyscript setup. They can be changed through the integration configuration.

## Entities

The integration provides:

- **Smart Charging switch** — enables/disables planner operation. It is not a charger control.
- **State sensor** — reports the current planner state.
- **Data sensor** — exposes the complete current plan as state attributes.
- **Charging Allowed binary sensor** — read-only indication that the current planner window permits charging.

Home Assistant may assign the final entity IDs through its entity registry. Use the registry/UI when referencing entities from automations.

## Services

### `ev_planner.update`

Runs one planner update cycle.

The integration intentionally does **not** start its own periodic background loop. Call this service from your own automation when appropriate.

### `ev_planner.create_plan`

Creates a new charging plan from the current inputs.

### `ev_planner.replan`

Clears the current plan and creates a new one.

### `ev_planner.clear_plan`

Clears the current plan.

### `ev_planner.status`

Returns the current runtime status. The service supports Home Assistant response data.

### `ev_planner.dashboard`

Returns the current planner/dashboard data. The service supports Home Assistant response data.

## Recommended architecture

```text
Home Assistant automation
        │
        ├── EV connected / planning trigger
        ├── update EV SOC / required energy
        └── ev_planner.create_plan
                    │
                    ▼
             ┌─────────────┐
             │  EV Planner │
             └──────┬──────┘
                    │
             planner outputs
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
sensor.ev_planner_data   charging_allowed
                                │
                                ▼
                       Your HA automation
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
               charger on/off         current / phase
```

This separation is intentional. The planner is the decision layer; Home Assistant automations are the physical charger control layer.

## Planner output

`sensor.ev_planner_data` contains the plan summary and decision information, including:

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

Each decision contains the source hour and the charging settings calculated by the planner.

## Electrical limits

The current planner configuration uses:

- 230 V;
- 6–16 A integer charging current;
- 1-phase and 3-phase charging;
- maximum 11.04 kW;
- a configurable phase-switch limit with the planner's hard safety cap.

The planner reads the technical limits from its central configuration.

## Data sources

The integration is deliberately generic about the configured price and Solcast entities. The default setup was developed around Zonneplan price data and Solcast forecast sensors, but the entity IDs can be changed through the config flow.

## Troubleshooting

1. Open **Settings → Devices & services → EV Planner**.
2. Verify all configured input entities exist.
3. Check that departure time/day and required energy contain valid values.
4. Call `ev_planner.create_plan` manually.
5. Inspect `sensor.ev_planner_state` and `sensor.ev_planner_data`.
6. Check Home Assistant logs for `EV Planner`.
7. Verify that your automations, not the integration, operate the physical charger.

## Development

Install development dependencies:

```bash
python -m pip install -r requirements_test.txt
```

Run all tests:

```bash
pytest
```

Run linting:

```bash
ruff check .
```

The repository contains both core unit tests and Home Assistant integration tests. The Home Assistant tests use `pytest-homeassistant-custom-component`.

## Project layout

```text
.
├── custom_components/
│   └── ev_planner/
│       ├── __init__.py
│       ├── binary_sensor.py
│       ├── config_flow.py
│       ├── const.py
│       ├── manifest.json
│       ├── sensor.py
│       ├── services.yaml
│       ├── strings.json
│       ├── switch.py
│       ├── translations/
│       └── core/
├── tests/
├── .github/
│   └── workflows/
├── hacs.json
├── LICENSE
├── pyproject.toml
└── requirements_test.txt
```

## License

MIT. See [LICENSE](LICENSE).
