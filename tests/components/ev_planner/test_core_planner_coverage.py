from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.components.ev_planner.const import PLANNER_MODE_NORMAL, PLANNER_MODE_SOLAR_ONLY, PV_ROUNDING_DOWN
from homeassistant.components.ev_planner.core.logger import Logger
from homeassistant.components.ev_planner.core.models import Hour
from homeassistant.components.ev_planner.core.planner import ChargingDecision, ChargingPlan, EVPlanner, PlannerSettings


def make_hour(start, duration=1.0, price=0.10, pv=0.0):
    hour = Hour(start=start, end=start + dt.timedelta(hours=duration), price=price, price_raw=price * 10_000_000, tariff_group="normal", sustainability_score=50.0, hour_index=0)
    hour.pv_estimate = pv
    hour.pv_estimate10 = pv
    hour.pv_estimate90 = pv
    return hour


def make_settings(**changes):
    data = {
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
    data.update(changes)
    return PlannerSettings(**data)


def make_planner(**changes):
    return EVPlanner(SimpleNamespace(hours=[]), SimpleNamespace(hours=[]), make_settings(**changes), Logger())


async def test_planner_settings_validation_paths():
    base = {
        "energy_needed_kwh": 1.0,
        "departure_time": dt.datetime(2026, 9, 22, 12, tzinfo=dt.timezone.utc),
        "max_price": 0.1,
        "min_pv_kwh": 0.0,
        "solar_is_free": True,
        "max_charge_power_kw": 11.04,
        "max_phase_switches": 0,
        "planner_mode": PLANNER_MODE_NORMAL,
        "pv_rounding": PV_ROUNDING_DOWN,
    }
    for key, value in [("energy_needed_kwh", 0), ("departure_time", "bad"), ("departure_time", dt.datetime(2026, 9, 22, 12)), ("max_price", -1), ("min_pv_kwh", -1), ("max_charge_power_kw", 0), ("max_charge_power_kw", 99), ("max_phase_switches", -1), ("planner_mode", "bad"), ("pv_rounding", "bad")]:
        data = dict(base)
        data[key] = value
        with pytest.raises((ValueError, TypeError)):
            PlannerSettings(**data)

    planner = make_planner()
    for key, value in [("max_price", -1), ("max_charge_power_kw", 0), ("max_charge_power_kw", 99), ("max_phase_switches", -1)]:
        setattr(planner.settings, key, value)
        with pytest.raises(ValueError):
            planner._validate_settings()
        setattr(planner.settings, key, base[key])
    planner.settings.departure_time = "bad"
    with pytest.raises(TypeError):
        planner._validate_settings()
    planner.settings.departure_time = dt.datetime(2026, 9, 22, 12)
    with pytest.raises(ValueError):
        planner._validate_settings()


async def test_planner_time_energy_power_paths():
    tz = dt.timezone.utc
    planner = make_planner()
    now = dt.datetime.now(tz)
    planner.settings.departure_time = now + dt.timedelta(hours=2)
    filtered = planner._filter_available_time([hour(now - dt.timedelta(hours=2)), hour(now - dt.timedelta(minutes=30), pv=-1), hour(now + dt.timedelta(minutes=30), pv=20), hour(now + dt.timedelta(hours=3))])
    assert len(filtered) == 2
    assert filtered[0].start >= now - dt.timedelta(minutes=1)
    assert filtered[1].end <= planner.settings.departure_time

    planner.settings.planner_mode = PLANNER_MODE_SOLAR_ONLY
    planner.settings.min_pv_kwh = 2
    zero = make_hour(now, duration=0)
    result = planner._calculate_available_energy([zero, hour(now, pv=-1), hour(now, pv=20)])
    assert result[0].usable_pv == 0
    assert result[1].usable_pv == 0
    assert result[2].usable_pv <= 11.04

    negative = hour(now, pv=-2)
    negative.usable_pv = -2
    planner._prepare_hour(negative)
    assert negative.free_energy == 0
    high = hour(now, pv=20)
    high.usable_pv = 20
    planner._prepare_hour(high)
    assert high.free_energy <= 11.04
    planner.settings.solar_is_free = False
    planner._prepare_hour(zero)

    assert planner._maximum_power_for_phases(3) == 11.04
    assert planner._maximum_power_for_phases(1) == 3.68
    assert planner._hour_max_energy(zero, 3) == 0
    assert planner._hour_free_energy(zero, 3) == 0
    assert planner._pv_rate_kw(zero) == 0
    assert planner._valid_currents(3)
    assert planner._actual_power_for_current(0, 3) == 0
    assert planner._actual_power_for_current(6, 0) == 0
    assert planner._duration_to_timedelta(0.5) == dt.timedelta(minutes=30)


async def test_planner_optimize_and_build_paths():
    planner = make_planner()
    planner._optimize_hours([])
    empty = make_hour(dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc))
    planner.settings.energy_needed_kwh = 0
    planner._optimize_hours([empty])
    assert not empty.selected

    planner.settings.energy_needed_kwh = 1
    planner.settings.max_phase_switches = -1
    planner._optimize_hours([make_hour(dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc), pv=1)])
    planner.settings.max_phase_switches = 99
    planner._optimize_hours([make_hour(dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc), pv=1)])
    planner.settings.max_price = 0.01
    planner._optimize_hours([make_hour(dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc), price=0.50)])

    selected = make_hour(dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc), price=0.01)
    planner.settings.max_price = 0.10
    planner._prepare_hour(selected)
    planner._optimize_hours([selected])
    assert planner._build_decisions([selected])
    assert planner._build_plan([selected]).energy_planned_kwh > 0


async def test_planner_final_validation_paths():
    start = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    planner = make_planner(energy_needed_kwh=1.0, max_price=0.30, max_phase_switches=1)

    def make_plan(price=0.10, energy=1.0, free=1.0, paid=0.0, power=1.38, current=6, phases=1):
        item = make_hour(start, price=price, pv=1.0)
        item.original_start = start
        item.original_end = start + dt.timedelta(hours=1)
        decision = ChargingDecision(item, energy, free, paid, price, paid * price, True, "", power, current, phases)
        return ChargingPlan([decision], energy, energy, 0.0, free, paid, paid * price, True, start + dt.timedelta(hours=2), 0.30), decision

    plan, _ = make_plan()
    planner._validate_final_plan(plan)
    for mutate in [
        lambda d, p: setattr(p, "energy_planned_kwh", 2.0),
        lambda d, p: setattr(d, "phases", 2),
        lambda d, p: setattr(d, "charge_current_a", 5),
        lambda d, p: setattr(d, "charge_current_a", 17),
        lambda d, p: setattr(d, "charge_power_kw", 2.0),
        lambda d, p: setattr(d, "free_energy_kwh", 2.0),
        lambda d, p: setattr(d, "energy_kwh", 2.0),
        lambda d, p: setattr(d.hour, "start", start - dt.timedelta(minutes=2)),
        lambda d, p: setattr(d.hour, "end", start + dt.timedelta(hours=2)),
    ]:
        plan, decision = make_plan(price=0.50)
        mutate(decision, plan)
        with pytest.raises(ValueError):
            planner._validate_final_plan(plan)

    plan, _ = make_plan(power=11.04, current=16, phases=3, energy=11.04, free=0, paid=11.04)
    planner.settings.max_charge_power_kw = 3.68
    with pytest.raises(ValueError):
        planner._validate_final_plan(plan)

    planner.settings.max_charge_power_kw = 11.04
    plan, _ = make_plan(power=12.0, current=16, phases=3, energy=12, free=0, paid=12)
    with patch.object(planner, "_actual_power_for_current", return_value=12.0), patch.object(planner, "_maximum_power_for_phases", return_value=20.0):
        with pytest.raises(ValueError):
            planner._validate_final_plan(plan)

    plan, _ = make_plan(power=4.0, current=16, phases=1, energy=4, free=0, paid=4)
    with patch.object(planner, "_actual_power_for_current", return_value=4.0), patch.object(planner, "_maximum_power_for_phases", return_value=10.0):
        with pytest.raises(ValueError):
            planner._validate_final_plan(plan)

    plan, _ = make_plan(price=0.50, energy=1.38, free=0, paid=1.38)
    with pytest.raises(ValueError):
        planner._validate_final_plan(plan)

    plan, decision = make_plan(energy=4.14, free=0, paid=4.14, power=4.14, current=6, phases=3)
    item2 = make_hour(start + dt.timedelta(hours=1), price=0.10)
    item2.original_start = item2.start
    item2.original_end = item2.end
    decision2 = ChargingDecision(item2, 1.38, 0, 1.38, 0.10, 0.138, True, "", 1.38, 6, 1)
    plan.decisions.append(decision2)
    plan.energy_needed_kwh = 5.52
    plan.energy_planned_kwh = 5.52
    plan.paid_energy_kwh = 5.52
    plan.estimated_cost = 0.552
    planner.settings.max_phase_switches = 0
    with pytest.raises(ValueError):
        planner._validate_final_plan(plan)
