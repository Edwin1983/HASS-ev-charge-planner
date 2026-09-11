# EV Planner architecture

```text
                         HOME ASSISTANT
                              │
                 ┌────────────┴────────────┐
                 │                         │
          Config / options           User automations
                 │                         │
                 ▼                         │
          EVPlannerController              │
                 │                         │
        ┌────────┼─────────┐               │
        ▼        ▼         ▼               │
     Prices   Solcast   input helpers      │
        │        │         │               │
        └────────┴────┬────┘               │
                      ▼                    │
                   EVPlanner               │
                      │                    │
                      ▼                    │
                ChargingPlan               │
                      │                    │
                      ▼                    │
                 EVScheduler               │
                      │                    │
                      ▼                    │
                 EVStatus                  │
                      │                    │
                      ▼                    │
              sensors / binary sensor ────┘
```

The integration deliberately stops at planner output.

It does not:

- detect whether an EV is connected;
- start or stop the physical charger;
- set charging current;
- switch phases;
- implement a charger-specific protocol.

Home Assistant automations may use the planner output to perform those actions.

The planner itself is the unchanged planning engine in `core/planner.py`. The surrounding integration provides configuration, Home Assistant I/O, scheduling/runtime state, entities and services.
