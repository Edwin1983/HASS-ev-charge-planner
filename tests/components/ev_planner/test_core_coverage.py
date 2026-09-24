from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.components.ev_planner.const import (
    PLANNER_MODE_NORMAL,
    PLANNER_MODE_SOLAR_ONLY,
    PV_ROUNDING_DOWN,
    PV_ROUNDING_UP,
)
from homeassistant.components.ev_planner.core.ev_planner import EVPlannerController
from homeassistant.components.ev_planner.core.logger import Logger
from homeassistant.components.ev_planner.core.models import Hour
from homeassistant.components.ev_planner.core.planner import (
    ChargingDecision,
    ChargingPlan,
    PlannerSettings,
)


def make_hour(start: dt.datetime, hours: float = 1.0, price: float = 0.10, pv: float = 0.0) -> Hour:
    item = Hour(
        start=start,
        end=start + dt.timedelta(hours=hours),
        price=price,
        price_raw=price * 10_000_000,
        tariff_group="normal",
        sustainability_score=50.0,
        hour_index=0,
    )
    item.pv_estimate = pv
    item.pv_estimate10 = pv
    item.pv_estimate90 = pv
    item.usable_pv = max(0.0, pv)
    return item


def make_settings(**overrides):
    values = {
        "energy_needed_kwh": 5.0,
        "departure_time": dt.datetime.now().astimezone() + dt.timedelta(hours=3),
        "max_price": 0.30,
        "min_pv_kwh": 0.0,
        "solar_is_free": True,
        "max_charge_power_kw": 11.04,
        "max_phase_switches": 8,
        "planner_mode": PLANNER_MODE_NORMAL,
        "pv_rounding": PV_ROUNDING_DOWN,
    }
    values.update(overrides)
    return PlannerSettings(**values)


def make_controller_stub():
    controller = object.__new__(EVPlannerController)
    controller.logger = Logger()
    controller.config = {}
    controller.entry_id = "test-entry"
    controller.entities = {
        "departure": "input_datetime.departure",
        "departure_day": "input_select.departure_day",
        "energy_needed": "input_number.energy",
        "max_price": "input_number.price",
        "min_pv_kwh": "input_number.pv",
        "max_phase_switches": "input_number.switches",
        "pv_rounding": "input_select.rounding",
        "solar_enabled": "input_boolean.solar",
    }
    controller._hass = SimpleNamespace(
        loop=SimpleNamespace(call_soon_threadsafe=lambda *args: None)
    )
    controller.hass = SimpleNamespace(get_state=lambda entity_id: None)
    controller.status = SimpleNamespace(
        get_status=lambda: SimpleNamespace(charging_allowed=True),
        as_dict=lambda: {"charging_allowed": True},
        update=lambda now: None,
        log_status=lambda: None,
    )
    controller.scheduler = SimpleNamespace(
        clear_plan=lambda: None,
        set_plan=lambda plan: None,
        update=lambda now: False,
    )
    controller.last_plan = None
    controller.last_plan_time = None
    controller.last_published_state = None
    controller.sensor_state = ""
    controller.sensor_data = {}
    controller.charging_allowed = False
    controller.enabled = False
    return controller


async def test_controller_entry_id_and_native_helpers(hass):
    with pytest.raises(ValueError, match="entry_id"):
        EVPlannerController(hass, Logger())

    controller = make_controller_stub()
    controller._signal_sensor_update = lambda: None
    controller._planner_mode_entity_id = lambda: None
    assert controller._get_planner_mode() == PLANNER_MODE_NORMAL
    controller._planner_mode_entity_id = lambda: "select.mode"
    controller.hass.get_state = lambda entity_id: PLANNER_MODE_SOLAR_ONLY
    assert controller._get_planner_mode() == PLANNER_MODE_SOLAR_ONLY
    controller.hass.get_state = lambda entity_id: "invalid"
    assert controller._get_planner_mode() == PLANNER_MODE_NORMAL

    controller._pv_rounding_entity_id = lambda: None
    assert controller._get_pv_rounding() == PV_ROUNDING_DOWN
    controller._pv_rounding_entity_id = lambda: "select.rounding"
    controller.hass.get_state = lambda entity_id: PV_ROUNDING_UP
    assert controller._get_pv_rounding() == PV_ROUNDING_UP
    controller.hass.get_state = lambda entity_id: "invalid"
    assert controller._get_pv_rounding() == PV_ROUNDING_DOWN

    controller._native_entity_id = lambda platform, suffix, legacy=None: legacy
    controller.hass.get_state = lambda entity_id: "bad"
    assert controller._native_number_state("x", "legacy", 3.0) == 3.0
    controller.hass.get_state = lambda entity_id: "-2"
    assert controller._get_max_phase_switches() == 0
    controller.hass.get_state = lambda entity_id: "20"
    assert controller._get_max_charge_power_kw() == 11.04
    controller._native_number_state = lambda suffix, legacy, default: 0.5
    assert controller._get_max_charge_power_kw() == 1.0


async def test_controller_settings_validation_paths():
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.timezone.utc)
    controller = make_controller_stub()
    controller._native_entity_id = lambda platform, suffix, legacy=None: legacy
    controller._get_planner_mode = lambda: PLANNER_MODE_SOLAR_ONLY
    controller._get_pv_rounding = lambda: PV_ROUNDING_UP
    controller._get_max_phase_switches = lambda: 4
    controller._get_max_charge_power_kw = lambda: 11.04
    values = {
        "energy": 5.0,
        "price": 30.0,
        "pv": 1.0,
        "departure": "12:00:00",
        "day": "Vandaag",
    }
    controller.hass = SimpleNamespace(
        get_state=lambda entity: {
            "input_number.energy": str(values["energy"]),
            "input_number.price": str(values["price"]),
            "input_number.pv": str(values["pv"]),
            "input_datetime.departure": values["departure"],
            "input_select.departure_day": values["day"],
        }.get(entity)
    )
    controller._native_number_state = lambda suffix, legacy, default: {
        "input_number.energy": values["energy"],
        "input_number.price": values["price"],
        "input_number.pv": values["pv"],
    }.get(legacy, default)
    assert controller._get_settings(now) is not None

    for key, value in [
        ("energy", 0),
        ("price", -1),
        ("pv", -1),
        ("energy", "bad"),
        ("price", "bad"),
        ("pv", "bad"),
    ]:
        values[key] = value
        assert controller._get_settings(now) is None
        values[key] = {"energy": 5.0, "price": 30.0, "pv": 1.0}[key]
    values["departure"] = None
    assert controller._get_settings(now) is None
    values["departure"] = "12:00:00"
    values["day"] = "Ongeldig"
    assert controller._get_settings(now) is None
    values["day"] = "Morgen"
    settings = controller._get_settings(now)
    assert settings is not None
    assert settings.departure_time.date() == (now + dt.timedelta(days=1)).date()
    values["departure"] = "bad"
    assert controller._get_settings(now) is None
    values["departure"] = "2026-09-22T12:00:00"
    with patch.object(PlannerSettings, "__init__", side_effect=ValueError("bad settings")):
        assert controller._get_settings(now) is None


async def test_controller_publish_and_plan_data_paths():
    controller = make_controller_stub()
    controller._signal_sensor_update = lambda: None
    controller._publish_planner_state("A")
    controller._publish_planner_state("A")
    assert controller.sensor_state == "A"
    controller._update_native_sensor_data("B", {"x": 1})
    assert controller.sensor_data == {"state": "B", "attributes": {"x": 1}}
    controller._publish_charging_allowed()
    assert controller.charging_allowed is True
    controller._publish_plan_data()
    assert controller.sensor_data["state"] == "Geen planning"

    start = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    item = make_hour(start, pv=1.0)
    item.original_start = item.start
    item.original_end = item.end
    item.selected = True
    item.phases = 1
    item.charge_current_a = 6
    item.charge_power_w = 1380
    item.charge_energy = 1.38
    item.free_energy = 1.0
    item.paid_energy = 0.38
    item.reason = "test"
    decision = ChargingDecision(item, 1.38, 1.0, 0.38, 0.10, 0.038, True, "test", 1.38, 6, 1)
    plan = ChargingPlan([decision], 1.38, 1.38, 0.0, 1.0, 0.38, 0.038, True, start + dt.timedelta(hours=2), 0.30)
    controller.last_plan = plan
    controller._native_entity_id = lambda platform, suffix, legacy=None: legacy
    controller.hass.get_state = lambda entity: "8" if entity == "input_number.switches" else "on"
    controller._publish_plan_data()
    assert controller.sensor_data["state"] == "Planning compleet"
    assert controller.sensor_data["attributes"]["decisions"]
    controller._publish_planner_decision(None)
    controller._publish_planner_decision(decision)
    assert "Laden gepland" in controller.sensor_state
    decision.selected = False
    controller._publish_planner_decision(decision)
    assert "Niet laden" in controller.sensor_state


async def test_controller_create_update_replan_dashboard():
    controller = make_controller_stub()
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.timezone.utc)
    controller._is_enabled = lambda: False
    controller._publish_plan_data = lambda: None
    controller._publish_planner_state = lambda state: setattr(controller, "last_published_state", state)
    assert controller.create_plan(now) is None
    assert controller.last_plan is None
    controller._is_enabled = lambda: True
    controller._get_settings = lambda now: None
    assert controller.create_plan(now) is None
    controller._get_settings = lambda now: make_settings()
    controller.prices = SimpleNamespace(read=lambda: SimpleNamespace(hours=[]))
    controller.solcast = SimpleNamespace(read=lambda: SimpleNamespace())
    assert controller.create_plan(now) is None
    controller.prices = SimpleNamespace(read=lambda: SimpleNamespace(hours=[1]))
    controller.solcast = SimpleNamespace(read=lambda: None)
    assert controller.create_plan(now) is None
    controller.solcast = SimpleNamespace(read=lambda: SimpleNamespace())
    controller._publish_plan_data = lambda: None
    with patch("homeassistant.components.ev_planner.core.ev_planner.EVPlanner.create_plan", side_effect=RuntimeError("boom")):
        assert controller.create_plan(now) is None
    with patch("homeassistant.components.ev_planner.core.ev_planner.EVPlanner.create_plan", return_value=None):
        assert controller.create_plan(now) is None
    controller.last_plan = SimpleNamespace(decisions=[])
    controller._get_current_decision = lambda now: None
    controller._publish_planner_decision = lambda decision: None
    controller.scheduler.update = lambda now: True
    controller.status.update = lambda now: None
    controller._publish_charging_allowed = lambda: None
    controller.update(now)
    controller.clear_plan()
    controller._is_enabled = lambda: True
    with patch.object(controller, "create_plan", return_value=None):
        assert controller.replan(now) is None
    assert controller.get_dashboard_data(now)["has_plan"] is False
    assert controller.get_status() == {"charging_allowed": True}
