from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from homeassistant.components.ev_planner.const import PV_ROUNDING_DOWN, PV_ROUNDING_UP
from homeassistant.components.ev_planner.core.logger import Logger
from homeassistant.components.ev_planner.core.models import Hour
from homeassistant.components.ev_planner.core.planner import ChargingPlan, EVPlanner, PlannerSettings
from homeassistant.components.ev_planner.core.prices import PriceReader
from homeassistant.components.ev_planner.core.scheduler import EVScheduler, SchedulerSettings
from homeassistant.components.ev_planner.core.solcast import SolcastReader
from homeassistant.components.ev_planner.core.solar import _option, apply_solar_only


def make_hour(start, duration=1.0, price=0.10, pv=0.0):
    item = Hour(start=start, end=start + dt.timedelta(hours=duration), price=price, price_raw=price * 10_000_000, tariff_group="normal", sustainability_score=50.0, hour_index=0)
    item.pv_estimate = pv
    item.pv_estimate10 = pv
    item.pv_estimate90 = pv
    item.usable_pv = max(0.0, pv)
    return item


async def test_scheduler_remaining_paths():
    settings = SchedulerSettings()
    scheduler = EVScheduler(settings=settings, logger=Logger())
    now = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    assert scheduler.get_current_hour(now) is None
    assert scheduler.charging_allowed(now) is False
    assert scheduler.selected_hours() == []
    assert scheduler.next_selected_hour(now) is None
    h1 = make_hour(now)
    h1.selected = False
    h2 = make_hour(now + dt.timedelta(hours=1), pv=1)
    h2.selected = True
    h2.charge_energy = settings.min_charge_energy
    scheduler.plan = SimpleNamespace(decisions=[SimpleNamespace(hour=h1, selected=False), SimpleNamespace(hour=h2, selected=True)])
    assert scheduler.get_current_hour(now) is None
    assert scheduler.selected_hours() == [h2]
    assert scheduler.next_selected_hour(now) == h2
    assert scheduler.charging_allowed(now) is False
    assert scheduler.charging_allowed(now + dt.timedelta(hours=1, minutes=1)) is True
    assert scheduler.charging_allowed(now + dt.timedelta(hours=2, minutes=1)) is False
    assert scheduler.get_status() is scheduler.status
    plan = ChargingPlan([], 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, False, now + dt.timedelta(hours=2), 0.30)
    scheduler.set_plan(plan)


async def test_solar_remaining_paths():
    planner = SimpleNamespace(
        settings=SimpleNamespace(pv_rounding=PV_ROUNDING_DOWN, min_pv_kwh=0.0, max_phase_switches=8),
        _hour_duration=lambda item: (item.end - item.start).total_seconds() / 3600,
        _valid_currents=lambda phases: [],
        _actual_power_for_current=lambda current, phases: current * 0.1,
    )
    item = make_hour(dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc), pv=1)
    assert _option(planner, item, 1) is None
    planner._valid_currents = lambda phases: [6, 7]
    assert _option(planner, item, 1) is not None
    planner.settings.pv_rounding = PV_ROUNDING_UP
    assert _option(planner, item, 1) is not None
    item.pv_estimate = 10
    assert _option(planner, item, 1) is not None
    planner.settings.pv_rounding = "bad"
    with pytest.raises(ValueError):
        _option(planner, item, 1)
    data = SimpleNamespace(settings=SimpleNamespace(energy_needed_kwh=5, max_phase_switches=8))
    apply_solar_only(data, [])
    assert data.settings.energy_needed_kwh == 0
    real = EVPlanner(
        SimpleNamespace(hours=[]),
        SimpleNamespace(hours=[]),
        PlannerSettings(
            energy_needed_kwh=1.0,
            departure_time=dt.datetime.now().astimezone() + dt.timedelta(hours=2),
            max_price=0.3,
            min_pv_kwh=0.0,
            solar_is_free=True,
            max_charge_power_kw=11.04,
            max_phase_switches=8,
            planner_mode="Normaal",
            pv_rounding=PV_ROUNDING_DOWN,
        ),
        Logger(),
    )
    apply_solar_only(real, [make_hour(dt.datetime.now().astimezone(), pv=2)])
    assert real.settings.energy_needed_kwh == 0


async def test_solcast_remaining_paths():
    reader = SolcastReader(SimpleNamespace(get_attributes=lambda e: {}), Logger())
    assert reader._validate_series([]) is None
    now = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    result = reader._create_hour({
        "period_start": now.isoformat(),
        "period_end": (now + dt.timedelta(minutes=30)).isoformat(),
        "pv_estimate": -1,
        "pv_estimate10": -2,
        "pv_estimate90": -3,
    })
    assert result.pv_estimate == 0
    assert result.pv_estimate10 == 0
    assert result.pv_estimate90 == 0


async def test_prices_remaining_paths():
    now = dt.datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
    reader = PriceReader(SimpleNamespace(get_attributes=lambda e: {"forecast": []}, get_state=lambda e: "0.1"), Logger())
    reader._validate_series([reader._create_hour({"start_date": now.isoformat(), "electricity_price": 1_000_000})])
    hour1 = reader._create_hour({"start_date": now.isoformat(), "electricity_price": 1_000_000})
    hour2 = reader._create_hour({"start_date": (now + dt.timedelta(hours=1)).isoformat(), "electricity_price": 2_000_000})
    data = reader._calculate_statistics([hour1, hour2])
    assert data.highest_price == pytest.approx(0.2)
