"""Independent exhaustive oracle tests for the experimental quarter-hour optimizer.

The oracle deliberately does not call optimizer helpers. It enumerates every
full-quarter charging action (skip, 1F/3F, 6..16 A), applies the constraints
directly, and returns the cheapest feasible result. The scenarios use targets
that can be reached with full quarters, so the oracle does not need to model
partial terminal actions.
"""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from random import Random
from types import SimpleNamespace

from custom_components.ev_planner.core.config import (
    MAX_POWER_1PH,
    MAX_POWER_3PH,
    MIN_CURRENT,
    VOLTAGE,
)
from custom_components.ev_planner.core.quarter_hour_optimizer import (
    QuarterHourOptimizer,
)


QUARTER_HOURS = 0.25
ENERGY_TICK = 0.0025
TICKS_PER_KWH = 400


def _slots(prices, pv=None):
    if pv is None:
        pv = [0.0] * len(prices)

    start = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
    return [
        SimpleNamespace(
            start=start + timedelta(minutes=15 * index),
            end=start + timedelta(minutes=15 * index + 15),
            price=float(price),
            usable_pv=float(pv[index]),
        )
        for index, price in enumerate(prices)
    ]


def _ticks(kwh):
    return int(round(kwh * TICKS_PER_KWH))


def _power_kw(phases, current):
    return VOLTAGE * phases * current / 1000.0


def _oracle(slots, target_kwh, max_price, max_power_kw, max_phase_switches):
    target_ticks = _ticks(target_kwh)

    @lru_cache(maxsize=None)
    def search(index, energy_ticks, last_phase, switches):
        if energy_ticks >= target_ticks:
            return (0.0, energy_ticks, switches)

        if index == len(slots):
            return None

        slot = slots[index]
        best = search(index + 1, energy_ticks, last_phase, switches)

        for phases in (1, 3):
            power_limit = min(
                MAX_POWER_1PH if phases == 1 else MAX_POWER_3PH,
                max_power_kw,
            )
            for current in range(MIN_CURRENT, 17):
                power = _power_kw(phases, current)
                if power > power_limit + 0.000001:
                    continue

                energy = power * QUARTER_HOURS
                energy_ticks_added = _ticks(energy)
                if energy_ticks_added <= 0:
                    continue

                new_energy_ticks = energy_ticks + energy_ticks_added
                if new_energy_ticks > target_ticks:
                    continue

                new_switches = switches
                if last_phase and last_phase != phases:
                    new_switches += 1
                if new_switches > max_phase_switches:
                    continue

                free = min(energy, max(0.0, slot.usable_pv))
                paid = max(0.0, energy - free)

                if slot.price > max_price and paid > 0.000001:
                    continue

                tail = search(
                    index + 1,
                    new_energy_ticks,
                    phases,
                    new_switches,
                )
                if tail is None:
                    continue

                candidate = (
                    paid * slot.price + tail[0],
                    tail[1],
                    tail[2],
                )
                if best is None or candidate[0] < best[0] - 0.000000001:
                    best = candidate

        return best

    return search(0, 0, 0, 0)


def _assert_dp_matches_oracle(
    prices,
    pv,
    target,
    max_price=1.0,
    max_power=11.04,
    switches=8,
):
    slots = _slots(prices, pv)
    oracle = _oracle(slots, target, max_price, max_power, switches)
    plan = QuarterHourOptimizer(
        energy_needed_kwh=target,
        max_price=max_price,
        max_charge_power_kw=max_power,
        max_phase_switches=switches,
    ).optimize(slots)

    assert oracle is not None
    assert plan.complete
    assert abs(plan.energy_kwh - target) < ENERGY_TICK + 0.000001
    assert abs(plan.cost - oracle[0]) < 0.000001


def test_oracle_matches_cheap_quarter_choice():
    _assert_dp_matches_oracle(
        prices=[0.50, 0.10, 0.20],
        pv=[0.0, 0.0, 0.0],
        target=0.345,
        max_price=0.50,
        switches=0,
    )


def test_oracle_matches_hard_price_limit():
    _assert_dp_matches_oracle(
        prices=[0.60, 0.20, 0.50],
        pv=[0.0, 0.0, 0.0],
        target=0.345,
        max_price=0.25,
        switches=0,
    )


def test_oracle_matches_pv_safe_price_capped_plan():
    _assert_dp_matches_oracle(
        prices=[0.80, 0.10, 0.70],
        pv=[0.40, 0.0, 0.40],
        target=1.38,
        max_price=0.25,
        switches=0,
    )


def test_oracle_matches_phase_switch_budget():
    _assert_dp_matches_oracle(
        prices=[0.10, 0.40, 0.11, 0.39],
        pv=[0.0, 0.0, 0.0, 0.0],
        target=1.38,
        max_price=0.50,
        switches=0,
    )


def test_oracle_matches_negative_price():
    _assert_dp_matches_oracle(
        prices=[-0.10, 0.20, 0.30],
        pv=[0.0, 0.0, 0.0],
        target=0.345,
        max_price=0.0,
        switches=0,
    )


def test_oracle_random_small_matrix():
    rng = Random(20260928)

    for _ in range(100):
        prices = [
            round(rng.choice([-0.10, 0.05, 0.12, 0.25, 0.45, 0.80]), 2)
            for _ in range(4)
        ]
        pv = [
            round(rng.choice([0.0, 0.0, 0.10, 0.20, 0.40]), 2)
            for _ in range(4)
        ]
        target = rng.choice([0.345, 0.69, 1.035, 1.38])
        max_price = rng.choice([0.0, 0.15, 0.30, 1.0])
        max_power = rng.choice([3.68, 11.04])
        switches = rng.choice([0, 1, 2])

        _assert_dp_matches_oracle(
            prices=prices,
            pv=pv,
            target=target,
            max_price=max_price,
            max_power=max_power,
            switches=switches,
        )
