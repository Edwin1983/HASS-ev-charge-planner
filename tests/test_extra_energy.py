"""Minimum/maximum benodigde energie: extra laden alleen goedkoop of gratis."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

from custom_components.ev_planner.core.ev_planner import EVPlannerController
from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings


def _base() -> datetime:
    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    return now - timedelta(minutes=now.minute % 15) + timedelta(minutes=15)


def _prices(values):
    base = _base()
    hours = []
    for i, price in enumerate(values):
        start = base + timedelta(minutes=15 * i)
        hours.append(
            Hour(
                start=start,
                end=start + timedelta(minutes=15),
                price=price,
                price_raw=price * 10_000_000,
                tariff_group="x",
                hour_index=i,
            )
        )
    data = PriceData()
    data.hours = hours
    data.valid_until = hours[-1].end
    return data


def _controller(extra_price_ct=5.0, **attrs):
    controller = object.__new__(EVPlannerController)
    controller.logger = Logger()
    controller._plan_lock = threading.RLock()
    controller._native_number_state = lambda key, entity, default: (
        extra_price_ct if key == "extra_charge_price" else default
    )
    controller.__dict__.update(attrs)
    return controller


def _settings(prices, energy):
    return PlannerSettings(
        energy_needed_kwh=energy,
        departure_time=prices.hours[-1].end,
        max_price=0.40,
        max_phase_switches=8,
    )


def _run(values, minimum, maximum, extra_price_ct=5.0):
    controller = _controller(extra_price_ct)
    settings = _settings(_prices(values), minimum)
    base = EVPlanner(_prices(values), SolcastData(), settings, Logger()).create_plan()
    inputs = (_prices(values), SolcastData())
    return base, controller._plan_with_extra(base, settings, inputs, minimum, maximum)


PRICES = [0.30] * 12 + [0.02] * 16 + [0.25] * 12


def test_extra_energy_uses_only_cheap_slots_up_to_the_maximum():
    base, plan = _run(PRICES, 5.0, 12.0)

    assert plan.complete and plan.missing_energy_kwh == 0
    assert abs(plan.energy_planned_kwh - 12.0) < 0.05
    assert abs(plan.extra_energy_kwh - 7.0) < 0.05
    assert plan.energy_needed_kwh == 5.0
    assert all(d.hour.price <= 0.05 + 1e-9 for d in plan.decisions if d.selected)


def test_no_extra_when_not_enough_cheap_energy_for_the_minimum():
    values = [0.30] * 12 + [0.02] * 2 + [0.30] * 20  # 2 goedkope kwartieren
    base, plan = _run(values, 8.0, 14.0)

    assert plan is base  # minimumplan blijft
    assert plan.extra_energy_kwh == 0.0


def test_extra_price_limit_is_respected():
    # 0,10 EUR is niet goedkoop genoeg bij een limiet van 5 ct
    values = [0.30] * 12 + [0.10] * 16 + [0.30] * 12
    base, plan = _run(values, 5.0, 12.0, extra_price_ct=5.0)
    assert plan is base

    base, plan = _run(values, 5.0, 12.0, extra_price_ct=12.0)
    assert plan.extra_energy_kwh > 6.0


def test_negative_prices_are_free_extra_energy():
    values = [0.30] * 12 + [-0.05] * 16 + [0.30] * 12
    base, plan = _run(values, 5.0, 12.0, extra_price_ct=0.0)
    assert abs(plan.energy_planned_kwh - 12.0) < 0.05


def test_incomplete_minimum_plan_gets_no_extra():
    values = [0.50] * 40  # alles boven max_price: minimum niet haalbaar
    controller = _controller()
    prices = _prices(values)
    settings = _settings(prices, 5.0)
    base = EVPlanner(_prices(values), SolcastData(), settings, Logger()).create_plan()
    plan = controller._plan_with_extra(
        base, settings, (_prices(values), SolcastData()), 5.0, 12.0
    )
    assert plan is base


##############################################################################
# Grenzen en sessie
##############################################################################


def _bounds_controller(minimum, maximum, override=None, delivered=None):
    values = {"energy_needed": minimum, "max_energy_needed": maximum}
    return _controller(
        entities={"energy_needed": "x"},
        energy_override_kwh=override,
        energy_delivered_kwh=delivered,
        _native_number_state=lambda key, entity, default: values.get(key, default),
    )


def test_bounds_without_session_come_from_the_entities():
    assert _bounds_controller(10, 25)._energy_bounds() == (10, 25)
    assert _bounds_controller(10, 0)._energy_bounds() == (10, 10)  # 0 = uit
    assert _bounds_controller(10, 5)._energy_bounds() == (10, 10)  # max < min


def test_bounds_during_a_session_subtract_what_was_delivered():
    controller = _bounds_controller(10, 25, delivered=12.0)
    assert controller._energy_bounds() == (0.0, 13.0)

    controller = _bounds_controller(10, 25, delivered=5.0)
    assert controller._energy_bounds() == (5.0, 20.0)


def test_session_target_is_the_minimum_and_goal_the_maximum():
    assert _bounds_controller(10, 25).get_entity_energy_target() == 10
    assert _bounds_controller(10, 25).get_entity_energy_goal() == 25
    assert _bounds_controller(10, 0).get_entity_energy_target() == 10
    assert _bounds_controller(10, 0).get_entity_energy_goal() == 10
    assert _bounds_controller(0, 20).get_entity_energy_target() == 20  # alles extra
    assert _bounds_controller(0, 0).get_entity_energy_target() is None


def test_override_without_known_target_behaves_as_before():
    assert _bounds_controller(10, 25, override=4.0)._energy_bounds() == (4.0, 4.0)


def test_planner_without_cheap_slots_gives_no_energy_or_a_clean_error():
    values = [0.30] * 40
    prices = _prices(values)
    settings = PlannerSettings(
        energy_needed_kwh=6.0,
        departure_time=prices.hours[-1].end,
        max_price=0.05,
        max_phase_switches=8,
    )
    try:
        plan = EVPlanner(prices, SolcastData(), settings, Logger()).create_plan()
    except Exception:
        return  # create_plan vangt dit af (planner-fout -> geen plan)
    assert plan.energy_planned_kwh <= 0.001
