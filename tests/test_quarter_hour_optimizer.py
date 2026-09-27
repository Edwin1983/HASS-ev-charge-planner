from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
                price=price,
                usable_pv=pv[index],
            )
        )
    return result


def test_cheap_quarter_is_selected_before_expensive_quarter():
    slots = _slots([0.50, 0.10, 0.20, 0.30])
    optimizer = QuarterHourOptimizer(
        energy_needed_kwh=0.345,
        max_price=0.40,
        max_phase_switches=0,
    )

    plan = optimizer.optimize(slots)

    assert plan.complete
    assert plan.energy_kwh == 0.345
    assert len(plan.actions) == 1
    assert plan.actions[0].index == 1
    assert abs(plan.actions[0].cost - 0.0345) < 0.000001


def test_max_price_is_a_hard_per_kwh_limit():
    slots = _slots([0.60, 0.55, 0.50, 0.45])
    optimizer = QuarterHourOptimizer(
        energy_needed_kwh=0.345,
        max_price=0.40,
        max_phase_switches=0,
    )

    plan = optimizer.optimize(slots)

    assert not plan.complete
    assert plan.energy_kwh == 0.0
    assert plan.missing_energy_kwh == 0.345


def test_expensive_slot_is_allowed_when_pv_covers_all_energy():
    slots = _slots([0.80], pv=[1.0])
    optimizer = QuarterHourOptimizer(
        energy_needed_kwh=0.345,
        max_price=0.10,
        max_phase_switches=0,
    )

    plan = optimizer.optimize(slots)

    assert plan.complete
    assert plan.paid_energy_kwh == 0.0
    assert plan.cost == 0.0
    assert plan.actions[0].free_energy_kwh == 0.345


def test_expensive_slot_cannot_mix_pv_and_grid_above_price_limit():
    slots = _slots([0.80], pv=[0.10])
    optimizer = QuarterHourOptimizer(
        energy_needed_kwh=0.345,
        max_price=0.10,
        max_phase_switches=0,
    )

    plan = optimizer.optimize(slots)

    assert not plan.complete
    assert plan.energy_kwh == 0.0


def test_phase_switch_budget_is_respected():
    slots = _slots([0.10, 0.20, 0.10, 0.20])
    optimizer = QuarterHourOptimizer(
        energy_needed_kwh=1.38,
        max_price=0.30,
        max_phase_switches=0,
    )

    plan = optimizer.optimize(slots)

    assert plan.complete
    assert plan.phase_switches == 0
    phases = {action.phases for action in plan.actions}
    assert len(phases) == 1


def test_final_quarter_can_be_partial():
    slots = _slots([0.10])
    optimizer = QuarterHourOptimizer(
        energy_needed_kwh=0.20,
        max_price=0.30,
        max_phase_switches=0,
    )

    plan = optimizer.optimize(slots)

    assert plan.complete
    assert abs(plan.energy_kwh - 0.20) < 0.000001
    assert len(plan.actions) == 1
    assert plan.actions[0].end < plan.actions[0].start + timedelta(minutes=15)


def test_negative_price_is_still_valid():
    slots = _slots([-0.10, 0.20])
    optimizer = QuarterHourOptimizer(
        energy_needed_kwh=0.345,
        max_price=0.00,
        max_phase_switches=0,
    )

    plan = optimizer.optimize(slots)

    assert plan.complete
    assert plan.actions[0].index == 0
    assert plan.cost < 0.0
