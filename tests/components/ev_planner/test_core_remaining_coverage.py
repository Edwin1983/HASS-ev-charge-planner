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
    EVPlanner,
    PlannerSettings,
)
from homeassistant.components.ev_planner.core.prices import PriceReader
from homeassistant.components.ev_planner.core.scheduler import EVScheduler, SchedulerSettings
from homeassistant.components.ev_planner.core.solcast import SolcastReader
from homeassistant.components.ev_planner.core.solar import _option, apply_solar_only


def make_hour(
    start: dt.datetime,
    duration: float = 1.0,
    price: float = 0.10,
    pv: float = 0.0,
) -> Hour:
    hour = Hour(
        start=start,
        end=start + dt.timedelta(hours=duration),
        price=price,
        price_raw=price * 10_000_000,
        tariff_group="normal",
        sustainability_score=50.0,
        hour_index=0,
    )
    hour.pv_estimate = pv
    hour.pv_estimate10 = pv
    hour.pv_estimate90 = pv
    hour.usable_pv = max(0.0, pv)
    return hour


def make_settings(**changes):
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
    values.update(changes)
    return PlannerSettings(**values)


def make_planner(**changes):
    return EVPlanner(
        SimpleNamespace(hours=[]),
        SimpleNamespace(hours=[]),
        make_settings(**changes),
        Logger(),
    )


def make_controller():
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
    controller.hass = SimpleNamespace(get_state=lambda entity: None)
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


async def test_controller_native_registry_branches(hass):
    controller = make_controller()
    registry = SimpleNamespace(
        async_get_entity_id=lambda platform, domain, unique_id: {
            "test-entry_planner_mode": "select.native_mode",
            "test-entry_pv_rounding": "select.native_rounding",
            "test-entry_smart_charging": "switch.native_smart",
            "test-entry_max_phase_switches": "number.native_switches",
        }.get(unique_id)
    )
    with patch(
        "homeassistant.components.ev_planner.core.ev_planner.er.async_get",
        return_value=registry,
    ):
        assert controller._planner_mode_entity_id() == "select.native_mode"
        assert controller._pv_rounding_entity_id() == "select.native_rounding"
        assert controller._smart_charging_entity_id() == "switch.native_smart"
        assert (
            controller._native_entity_id(
                "number", "max_phase_switches", "legacy"
            )
            == "number.native_switches"
        )


async def test_controller_create_success_and_default_now():
    controller = make_controller()
    controller._signal_sensor_update = lambda: None
    controller._is_enabled = lambda: True
    controller._get_settings = lambda now: make_settings()
    controller.prices = SimpleNamespace(read=lambda: SimpleNamespace(hours=[1]))
    controller.solcast = SimpleNamespace(read=lambda: SimpleNamespace())
    start = dt.datetime.now().astimezone().replace(microsecond=0)
    hour = make_hour(start)
    decision = ChargingDecision(
        hour, 1.38, 1.0, 0.38, 0.10, 0.038, True, "test", 1.38, 6, 1
    )
    plan = ChargingPlan(
        [decision],
        1.38,
        1.38,
        0.0,
        1.0,
        0.38,
        0.038,
        True,
        start + dt.timedelta(hours=2),
        0.30,
    )
    controller.scheduler.set_plan = lambda value: None
    controller._publish_plan_data = lambda: None
    controller._publish_planner_decision = lambda value: None
    with patch(
        "homeassistant.components.ev_planner.core.ev_planner.EVPlanner.create_plan",
        return_value=plan,
    ):
        result = controller.create_plan()
    assert result is plan
    assert controller.last_plan is plan


async def test_controller_update_default_and_create_failure():
    controller = make_controller()
    controller._publish_plan_data = lambda: None
    controller._publish_planner_state = lambda state: None
    controller._is_enabled = lambda: False
    controller.update()
    assert controller.last_plan is None

    controller._is_enabled = lambda: True
    controller.create_plan = lambda now: None
    controller.status.update = lambda now: None
    controller._publish_charging_allowed = lambda: None
    controller.update()
    assert controller.last_plan is None


async def test_controller_phase_decision_dashboard_paths():
    controller = make_controller()
    start = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    h1 = make_hour(start)
    h2 = make_hour(start + dt.timedelta(hours=1))
    h3 = make_hour(start + dt.timedelta(hours=2))
    d1 = ChargingDecision(h1, 1, 0, 1, 0.1, 0.1, True, "", 1.38, 6, 1)
    d2 = ChargingDecision(h2, 1, 0, 1, 0.1, 0.1, True, "", 5.52, 8, 3)
    d3 = ChargingDecision(h3, 1, 0, 1, 0.1, 0.1, True, "", 5.52, 8, 3)
    plan = ChargingPlan(
        [d1, d2, d3],
        3,
        3,
        0,
        0,
        3,
        0.3,
        True,
        start + dt.timedelta(hours=4),
        0.3,
    )
    assert controller._count_plan_phase_switches(None) == 0
    assert controller._count_plan_phase_switches(plan) == 1
    controller.last_plan = None
    assert controller._get_current_decision(start) is None
    controller.last_plan = plan
    assert controller._get_current_decision(start + dt.timedelta(minutes=10)) is d1
    assert controller._get_current_decision(start + dt.timedelta(hours=3)) is None

    bad_hour = SimpleNamespace(
        start=start,
        end=start + dt.timedelta(hours=1),
        pv_estimate="bad",
        usable_pv=object(),
    )
    bad_decision = SimpleNamespace(
        hour=bad_hour,
        energy_kwh=1,
        free_energy_kwh=0,
        paid_energy_kwh=1,
        price=0.1,
        cost=0.1,
        charge_power_kw=1.38,
        charge_current_a=6,
        phases=1,
        selected=True,
        reason="x",
    )
    data = controller._decision_to_dict(bad_decision)
    assert data["pv_estimate_kwh"] == 0.0
    assert data["usable_pv_kwh"] == 0.0

    controller.last_plan = plan
    controller._native_number_state = lambda suffix, legacy, default: 8
    plan.decisions.append(
        ChargingDecision(
            SimpleNamespace(start=h3.start, end=h3.start),
            0,
            0,
            0,
            0,
            0,
            False,
            "",
            0,
            0,
            1,
        )
    )
    result = controller._plan_to_dict(plan)
    assert result["phase_switches"] == 1
    assert result["total_charging_minutes"] == 180.0

    controller.status.as_dict = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    dashboard = controller.get_dashboard_data(start)
    assert dashboard["status"] == {}


async def test_planner_create_build_and_solcast_paths():
    start = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    price = make_hour(start, price=0.1)
    solar = make_hour(start, pv=2.0)
    planner = make_planner(
        energy_needed_kwh=1.0,
        departure_time=start + dt.timedelta(hours=2),
    )
    planner.prices.hours = [price]
    planner.solcast.hours = [solar]
    assert planner._find_solcast_hour(start) is solar
    assert planner._find_solcast_hour(start + dt.timedelta(hours=2)) is None
    assert planner._build_hour_list()[0].pv_estimate == 2.0

    planner.solcast.hours = []
    assert planner._build_hour_list()[0].pv_estimate == 0.0

    planner.settings.planner_mode = PLANNER_MODE_SOLAR_ONLY
    planner.settings.energy_needed_kwh = 1.0
    planner.prices.hours = [make_hour(start, pv=2.0)]
    with patch(
        "homeassistant.components.ev_planner.core.planner.apply_solar_only"
    ) as solar_only:
        with patch.object(planner, "_validate_final_plan"):
            with patch.object(planner, "_log_plan"):
                result = planner.create_plan()
    solar_only.assert_called_once()
    assert result is not None


async def test_planner_filter_complete_and_combine_paths():
    start = dt.datetime.now().astimezone().replace(microsecond=0)
    planner = make_planner(departure_time=start + dt.timedelta(hours=3))
    full = make_hour(start + dt.timedelta(hours=1))
    future = make_hour(start + dt.timedelta(hours=4))
    filtered = planner._filter_available_time([full, future])
    assert filtered == [full]
    combined = planner._combine_hour_data(full, None)
    assert combined.pv_estimate == 0.0


async def test_planner_remaining_optimization_paths():
    planner = make_planner()
    planner._optimize_hours([])
    planner.settings.energy_needed_kwh = 0
    hour = make_hour(dt.datetime.now().astimezone())
    planner._optimize_hours([hour])
    assert not hour.selected

    planner.settings.energy_needed_kwh = 1
    planner.settings.max_price = 0.01
    expensive = make_hour(dt.datetime.now().astimezone(), price=0.50)
    planner._optimize_hours([expensive])
    assert not expensive.selected


async def test_solar_remaining_branch_paths():
    planner = SimpleNamespace(
        settings=SimpleNamespace(
            pv_rounding=PV_ROUNDING_DOWN,
            min_pv_kwh=0.0,
            max_phase_switches=8,
        ),
        _hour_duration=lambda item: (
            item.end - item.start
        ).total_seconds()
        / 3600,
        _valid_currents=lambda phases: [6, 7],
        _actual_power_for_current=lambda current, phases: current * phases * 0.23,
    )
    hour = make_hour(
        dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc), pv=10
    )
    assert _option(planner, hour, 1) is not None
    planner.settings.pv_rounding = PV_ROUNDING_UP
    assert _option(planner, hour, 1) is not None
    planner.settings.pv_rounding = "bad"
    with pytest.raises(ValueError):
        _option(planner, hour, 1)
    empty = SimpleNamespace(
        settings=SimpleNamespace(energy_needed_kwh=5, max_phase_switches=8)
    )
    apply_solar_only(empty, [])
    assert empty.settings.energy_needed_kwh == 0


async def test_sensor_current_decision_invalid_records():
    from homeassistant.components.ev_planner.sensor import (
        EVPlannerChargeCurrentSensor,
        EVPlannerPhasesSensor,
        _current_decision,
    )

    now = dt.datetime.now().astimezone()
    data = {
        "attributes": {
            "decisions": [
                {"start": "bad", "end": "bad"},
                {
                    "start": (now + dt.timedelta(hours=1)).isoformat(),
                    "end": (now + dt.timedelta(hours=2)).isoformat(),
                },
            ]
        }
    }
    assert _current_decision(data) is None

    controller = SimpleNamespace(sensor_data={"attributes": {"decisions": []}})
    entry = SimpleNamespace(entry_id="test-entry", runtime_data=controller)
    assert EVPlannerChargeCurrentSensor(None, entry).native_value == 0
    assert EVPlannerPhasesSensor(None, entry).native_value == 0


async def test_remaining_reader_paths():
    reader = SolcastReader(SimpleNamespace(get_attributes=lambda entity: {}), Logger())
    assert reader._validate_series([]) is None
    now = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    solar_hour = reader._create_hour(
        {
            "period_start": now.isoformat(),
            "period_end": (now + dt.timedelta(minutes=30)).isoformat(),
            "pv_estimate": -1,
            "pv_estimate10": -2,
            "pv_estimate90": -3,
        }
    )
    assert solar_hour.pv_estimate == 0
    assert solar_hour.pv_estimate10 == 0
    assert solar_hour.pv_estimate90 == 0

    price_reader = PriceReader(
        SimpleNamespace(
            get_attributes=lambda entity: {"forecast": []},
            get_state=lambda entity: "0.1",
        ),
        Logger(),
    )
    hour1 = price_reader._create_hour(
        {"start_date": now.isoformat(), "electricity_price": 1_000_000}
    )
    hour2 = price_reader._create_hour(
        {
            "start_date": (now + dt.timedelta(hours=1)).isoformat(),
            "electricity_price": 2_000_000,
        }
    )
    stats = price_reader._calculate_statistics([hour1, hour2])
    assert stats.highest_price == pytest.approx(0.2)

    scheduler = EVScheduler(SchedulerSettings(), Logger())
    assert scheduler.get_current_hour(now) is None
    assert scheduler.selected_hours() == []
