"""Cross-check the experimental quarter-hour optimizer against production."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings
from custom_components.ev_planner.core.quarter_hour_optimizer import (
    QuarterHourOptimizer,
)


def _slots(prices, pv=None):
    if pv is None:
        pv = [0.0] * len(prices)

    start = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
    result = []
    for index, price in enumerate(prices):
        begin = start + timedelta(minutes=15 * index)
        result.append(
            SimpleNamespace(
                start=begin,
                end=begin + timedelta(minutes=15),
                price=float(price),
                usable_pv=float(pv[index]),
            )
        )
    return result


def _run_production(slots, energy_needed, max_price, max_power=11.04, switches=2):
    hours = [
        Hour(
            start=slot.start,
            end=slot.end,
            price=slot.price,
            usable_pv=slot.usable_pv,
        )
        for slot in slots
    ]
    settings = PlannerSettings(
        energy_needed_kwh=energy_needed,
        departure_time=hours[-1].end,
        max_price=max_price,
        max_charge_power_kw=max_power,
        max_phase_switches=switches,
    )
    planner = EVPlanner(
        prices=PriceData(hours=hours),
        solcast=SolcastData(),
        settings=settings,
        logger=Logger(),
    )

    planner._calculate_available_energy(hours)
    for hour in hours:
        planner._prepare_hour(hour)
    planner._optimize_hours(hours)

    selected = [hour for hour in hours if hour.selected]
    energy = sum(hour.charge_energy for hour in selected)
    paid = sum(hour.paid_energy for hour in selected)
    cost = sum(hour.paid_energy * hour.price for hour in selected)

    return {
        "complete": energy >= energy_needed - 0.001,
        "energy": energy,
        "paid": paid,
        "cost": cost,
        "actions": selected,
    }


def _run_experimental(slots, energy_needed, max_price, max_power=11.04, switches=2):
    return QuarterHourOptimizer(
        energy_needed_kwh=energy_needed,
        max_price=max_price,
        max_charge_power_kw=max_power,
        max_phase_switches=switches,
    ).optimize(slots)


def _assert_same_objective(production, experimental, target):
    assert production["complete"] == experimental.complete
    assert abs(production["energy"] - experimental.energy_kwh) < 0.0026
    if experimental.complete:
        assert abs(experimental.energy_kwh - target) < 0.0026
        assert abs(production["cost"] - experimental.cost) < 0.00001
    assert abs(production["paid"] - experimental.paid_energy_kwh) < 0.0026


def test_dp_matches_production_on_cheap_and_expensive_quarters():
    slots = _slots([0.50, 0.10, 0.20, 0.30])
    production = _run_production(slots, 0.345, 0.40, switches=0)
    experimental = _run_experimental(slots, 0.345, 0.40, switches=0)

    _assert_same_objective(production, experimental, 0.345)
    assert experimental.actions[0].index == 1


def test_dp_matches_production_with_hard_price_limit():
    slots = _slots([0.60, 0.20, 0.50, 0.10])
    production = _run_production(slots, 1.38, 0.25, switches=0)
    experimental = _run_experimental(slots, 1.38, 0.25, switches=0)

    _assert_same_objective(production, experimental, 1.38)
    assert all(
        action.paid_energy_kwh == 0.0
        or slots[action.index].price <= 0.25
        for action in experimental.actions
    )


def test_dp_finds_cheaper_pv_safe_plan_than_production():
    slots = _slots(
        [0.80, 0.10, 0.70, 0.20],
        pv=[0.40, 0.0, 0.40, 0.0],
    )
    production = _run_production(slots, 1.38, 0.25, switches=0)
    experimental = _run_experimental(slots, 1.38, 0.25, switches=0)

    assert production["complete"]
    assert experimental.complete
    assert abs(production["energy"] - 1.38) < 0.0026
    assert abs(experimental.energy_kwh - 1.38) < 0.0026
    assert experimental.cost < production["cost"]
    assert abs(experimental.cost - 0.069) < 0.00001
    assert abs(experimental.paid_energy_kwh - 0.69) < 0.0026
    assert all(
        action.paid_energy_kwh == 0.0
        or slots[action.index].price <= 0.25
        for action in experimental.actions
    )


def test_dp_matches_production_with_phase_switch_budget():
    slots = _slots([0.10, 0.40, 0.11, 0.39, 0.12, 0.38])
    production = _run_production(slots, 5.52, 0.50, switches=1)
    experimental = _run_experimental(slots, 5.52, 0.50, switches=1)

    _assert_same_objective(production, experimental, 5.52)
    assert experimental.phase_switches <= 1


def test_dp_matches_production_on_full_57kwh_case():
    prices = [0.18, 0.42, 0.27, 0.11] * 15
    slots = _slots(prices)
    production = _run_production(
        slots,
        57.0,
        1.0,
        max_power=11.04,
        switches=8,
    )
    experimental = _run_experimental(
        slots,
        57.0,
        1.0,
        max_power=11.04,
        switches=8,
    )

    _assert_same_objective(production, experimental, 57.0)
    assert experimental.phase_switches <= 8
