from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from homeassistant.components.ev_planner.const import PV_ROUNDING_DOWN, PV_ROUNDING_UP
from homeassistant.components.ev_planner.core.logger import Logger
from homeassistant.components.ev_planner.core.models import Hour
from homeassistant.components.ev_planner.core.prices import PriceReader
from homeassistant.components.ev_planner.core.scheduler import EVScheduler, SchedulerSettings
from homeassistant.components.ev_planner.core.solcast import SolcastReader
from homeassistant.components.ev_planner.core.solar import _option, apply_solar_only


def make_hour(start, duration=1.0, price=0.10, pv=0.0):
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
    return hour


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
    scheduler.plan = SimpleNamespace(
        decisions=[
            SimpleNamespace(hour=h1, selected=False),
            SimpleNamespace(hour=h2, selected=True),
        ]
    )
    assert scheduler.get_current_hour(now) is None
    assert scheduler.selected_hours() == [h2]
    assert scheduler.next_selected_hour(now) == h2
    assert scheduler.charging_allowed(now) is False
    assert scheduler.charging_allowed(now + dt.timedelta(hours=1, minutes=1)) is False


async def test_solar_remaining_paths():
    planner = SimpleNamespace(
        settings=SimpleNamespace(
            pv_rounding=PV_ROUNDING_DOWN,
            min_pv_kwh=0.0,
            max_phase_switches=8,
        ),
        _hour_duration=lambda hour: (hour.end - hour.start).total_seconds() / 3600,
        _valid_currents=lambda phases: [],
        _actual_power_for_current=lambda current, phases: current * phases,
    )
    item = make_hour(dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc), pv=1)
    assert _option(planner, item, 1) is None
    planner._valid_currents = lambda phases: [6, 7]
    assert _option(planner, item, 1) is not None
    planner.settings.pv_rounding = PV_ROUNDING_UP
    assert _option(planner, item, 1) is not None
    planner.settings.pv_rounding = "bad"
    with pytest.raises(ValueError):
        _option(planner, item, 1)
    empty = SimpleNamespace(
        settings=SimpleNamespace(energy_needed_kwh=5, max_phase_switches=8)
    )
    apply_solar_only(empty, [])
    assert empty.settings.energy_needed_kwh == 0


async def test_solcast_remaining_paths():
    reader = SolcastReader(SimpleNamespace(get_attributes=lambda e: {}), Logger())
    assert reader._validate_series([]) is None
    now = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    item = {
        "period_start": now.isoformat(),
        "period_end": (now + dt.timedelta(minutes=30)).isoformat(),
        "pv_estimate": -1,
        "pv_estimate10": -2,
        "pv_estimate90": -3,
    }
    hour = reader._create_hour(item)
    assert hour.pv_estimate == 0
    assert hour.pv_estimate10 == 0
    assert hour.pv_estimate90 == 0


async def test_prices_remaining_paths():
    now = dt.datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
    reader = PriceReader(
        SimpleNamespace(
            get_attributes=lambda e: {"forecast": []},
            get_state=lambda e: "0.1",
        ),
        Logger(),
    )
    reader._validate_series(
        [
            reader._create_hour(
                {"start_date": now.isoformat(), "electricity_price": 1_000_000}
            )
        ]
    )
    hour1 = reader._create_hour(
        {"start_date": now.isoformat(), "electricity_price": 1_000_000}
    )
    hour2 = reader._create_hour(
        {
            "start_date": (now + dt.timedelta(hours=1)).isoformat(),
            "electricity_price": 2_000_000,
        }
    )
    data = reader._calculate_statistics([hour1, hour2])
    assert data.highest_price == pytest.approx(0.2)
