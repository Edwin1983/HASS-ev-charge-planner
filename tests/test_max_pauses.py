"""Maximaal aantal pauzes (onderbrekingen tussen laadmomenten)."""

from __future__ import annotations

import copy
import itertools
import random
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.ev_planner.core import planner_fast
from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings

START = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


def _hours(prices, step=15, pv=None):
    out = []
    for i, price in enumerate(prices):
        begin = START + timedelta(minutes=step * i)
        out.append(
            Hour(
                start=begin,
                end=begin + timedelta(minutes=step),
                price=price,
                pv_estimate=(pv[i] if pv else 0.0),
            )
        )
    return out


def _run(hours, target, max_pauses, use_fast, max_price=1.0, switches=8):
    settings = PlannerSettings(
        energy_needed_kwh=target,
        departure_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        max_price=max_price,
        max_phase_switches=switches,
        max_pauses=max_pauses,
    )
    planner = EVPlanner(PriceData(), SolcastData(), settings, Logger())
    original = planner_fast.available
    planner_fast.available = lambda: use_fast
    try:
        planner._optimize_hours(hours)
    finally:
        planner_fast.available = original
    return hours


def _pauses(hours):
    return EVPlanner._count_pauses(
        [("full",) if h.selected else ("skip",) for h in sorted(hours, key=lambda h: h.start)]
    )


def _cost(hours):
    return sum(h.paid_energy * h.price for h in hours if h.selected)


def _energy(hours):
    return sum(h.charge_energy for h in hours if h.selected)


def test_count_pauses():
    count = EVPlanner._count_pauses
    S, F = ("skip",), ("full", 1, 6)
    assert count([S, S, S]) == 0
    assert count([F, F, F]) == 0
    assert count([S, F, F, S, S]) == 0  # voor eerste / na laatste telt niet
    assert count([F, S, F]) == 1
    assert count([F, S, S, F, S, F]) == 2


def test_zero_pauses_gives_one_continuous_block():
    # Goedkoop, duur, goedkoop: zonder limiet wordt het duurste slot overgeslagen.
    prices = [0.05, 0.05, 0.50, 0.50, 0.05, 0.05, 0.50, 0.50]
    free = _run(_hours(prices), 6.0, None, False)
    assert _pauses(free) >= 1

    hours = _run(_hours(prices), 6.0, 0, False)
    assert _pauses(hours) == 0
    assert abs(_energy(hours) - 6.0) < 1e-6
    assert _cost(hours) >= _cost(free) - 1e-9


def test_limit_is_respected_and_cost_is_monotonic():
    rnd = random.Random(7)
    for _ in range(6):
        prices = [round(rnd.uniform(-0.05, 0.40), 3) for _ in range(12)]
        target = round(rnd.uniform(3, 14), 2)
        previous = None
        for limit in (0, 1, 2, 3, None):
            result = _run(_hours(prices), target, limit, False)
            if abs(_energy(result) - target) > 1e-5:
                previous = None
                break
            if limit is not None:
                assert _pauses(result) <= limit
            if previous is not None:
                assert _cost(result) <= previous + 1e-9
            previous = _cost(result)


def test_matches_subset_enumeration_reference():
    """Referentie: haalbare plannen uit alle toegestane slotpatronen."""
    rnd = random.Random(20261010)
    compared = 0

    for _ in range(14):
        n = rnd.choice([6, 7, 8])
        prices = [round(rnd.uniform(-0.05, 0.40), 3) for _ in range(n)]
        pv = [rnd.choice([0.0, 0.0, rnd.uniform(0, 1.5)]) for _ in range(n)]
        target = round(rnd.uniform(2.0, 0.9 * n * 2.76), 2)
        limit = rnd.choice([0, 1, 2])

        best = None
        for mask in itertools.product((0, 1), repeat=n):
            pattern = [i for i, bit in enumerate(mask) if bit]
            if not pattern:
                continue
            gaps = sum(
                1 for a, b in zip(pattern, pattern[1:]) if b != a + 1
            )
            if gaps > limit:
                continue
            restricted = list(prices)
            restricted_pv = list(pv)
            for i in range(n):
                if not mask[i]:
                    # geen laden mogelijk: te duur en geen gratis zon
                    restricted[i] = 999.0
                    restricted_pv[i] = 0.0
            result = _run(_hours(restricted, pv=restricted_pv), target, None, False, max_price=1.0)
            if abs(_energy(result) - target) > 1e-5:
                continue
            # Alleen plannen die echt het patroon volgen: de DP mag binnen
            # een patroon slots overslaan en zou dan meer pauzes krijgen.
            if [h.selected for h in sorted(result, key=lambda h: h.start)] != [
                bool(bit) for bit in mask
            ]:
                continue
            cost = sum(
                h.paid_energy * prices[i]
                for i, h in enumerate(sorted(result, key=lambda h: h.start))
                if h.selected
            )
            if best is None or cost < best:
                best = cost

        if best is None:
            continue

        for use_fast in (False, True):
            if use_fast and not planner_fast.available():
                continue
            got = _run(_hours(prices, pv=pv), target, limit, use_fast)
            assert abs(_energy(got) - target) < 1e-5
            assert _pauses(got) <= limit
            # Een haalbaar plan (best) is een bovengrens; de DP mag nooit
            # slechter zijn en nooit goedkoper dan het plan zonder limiet.
            assert _cost(got) <= best + 3e-4
            free = _run(_hours(prices, pv=pv), target, None, use_fast)
            assert _cost(got) >= _cost(free) - 3e-4
        compared += 1

    assert compared >= 8


def test_unlimited_and_large_limits_behave_like_before():
    rnd = random.Random(3)
    prices = [round(rnd.uniform(0, 0.4), 3) for _ in range(16)]
    a = _run(_hours(prices), 9.0, None, False)
    b = _run(_hours(prices), 9.0, 50, False)
    assert [h.selected for h in a] == [h.selected for h in b]
    assert abs(_cost(a) - _cost(b)) < 1e-12


def test_partial_plan_respects_the_limit():
    prices = [0.05, 0.50, 0.05, 0.50, 0.05, 0.50, 0.05, 0.50]
    hours = _run(_hours(prices), 40.0, 1, False, max_price=0.20)
    assert _pauses(hours) <= 1
    assert _energy(hours) > 0


def test_negative_limit_is_rejected():
    with pytest.raises(ValueError):
        PlannerSettings(
            energy_needed_kwh=5,
            departure_time=START + timedelta(hours=5),
            max_price=1.0,
            max_pauses=-1,
        )


def test_fast_path_matches_exact_dp_with_pause_limit():
    if not planner_fast.available():
        pytest.skip("numpy niet beschikbaar")

    rnd = random.Random(20261011)
    used = 0
    compared = 0
    original = planner_fast.fast_optimize

    def recording(**kwargs):
        result = original(**kwargs)
        recording.calls += 1
        if result is not None:
            recording.hits += 1
        return result

    recording.calls = 0
    recording.hits = 0
    planner_fast.fast_optimize = recording

    try:
        for _ in range(30):
            n = rnd.choice([6, 8, 10, 12])
            step = rnd.choice([15, 15, 60])
            prices = [round(rnd.uniform(-0.05, 0.40), 3) for _ in range(n)]
            pv = [rnd.choice([0.0, 0.0, rnd.uniform(0, 2.0)]) for _ in range(n)]
            hours = _hours(prices, step=step, pv=pv)
            if rnd.random() < 0.6:
                hours[0].start += timedelta(seconds=rnd.uniform(1, step * 60 - 60))
            if rnd.random() < 0.4:
                hours[-1].end -= timedelta(seconds=rnd.uniform(60, step * 60 - 60))
            infeasible = rnd.random() < 0.3
            capacity = n * (step / 60.0) * 11.04
            target = round(
                capacity * (rnd.uniform(1.05, 1.5) if infeasible else rnd.uniform(0.1, 0.6)), 2
            )
            max_price = rnd.choice([1.0, 0.30])
            limit = rnd.choice([0, 1, 2, 3])
            switches = rnd.choice([0, 2, 8])

            exact = _run(copy.deepcopy(hours), target, limit, False, max_price, switches)
            before = recording.hits
            fast = _run(copy.deepcopy(hours), target, limit, True, max_price, switches)
            used += recording.hits - before

            assert _pauses(fast) <= limit
            assert abs(_energy(fast) - _energy(exact)) < 1e-5
            assert _cost(fast) <= _cost(exact) + 3e-6
            compared += 1
    finally:
        planner_fast.fast_optimize = original

    assert compared == 30
    assert used >= 18  # de snelle route doet het meeste werk zelf


def _controller(value):
    from types import SimpleNamespace

    from custom_components.ev_planner.core.ev_planner import EVPlannerController

    controller = object.__new__(EVPlannerController)
    controller._native_number_state = lambda suffix, legacy, default: (
        default if value is None else value
    )
    return controller, SimpleNamespace


def test_controller_maps_the_number_to_a_limit():
    for raw, expected in ((None, None), (10.0, None), (12.0, None), (0.0, 0), (2.0, 2), (-3.0, 0)):
        controller, _ = _controller(raw)
        assert controller._get_max_pauses() == expected


def test_controller_counts_pauses_in_a_plan():
    from types import SimpleNamespace

    controller, _ = _controller(None)
    base = START

    def decision(i, selected=True):
        return SimpleNamespace(
            selected=selected,
            hour=SimpleNamespace(
                start=base + timedelta(minutes=15 * i),
                end=base + timedelta(minutes=15 * (i + 1)),
            ),
        )

    # slots 0,1 | 3 | 5,6  -> twee pauzes
    plan = SimpleNamespace(decisions=[decision(i) for i in (0, 1, 3, 5, 6)])
    assert controller._count_plan_pauses(plan) == 2
    assert controller._count_plan_pauses(SimpleNamespace(decisions=[decision(0), decision(1)])) == 0
    assert controller._count_plan_pauses(None) == 0


def test_controller_reads_and_clamps_the_progress_threshold():
    for raw, expected in ((None, 0.5), (1.2, 1.2), (0.0, 0.5), (9.0, 3.0), (0.01, 0.1)):
        controller, _ = _controller(raw)
        assert abs(controller.get_progress_threshold_kwh() - expected) < 1e-9


def test_controller_reads_and_clamps_the_forecast_markup():
    for raw, expected in ((None, 0.02), (3.5, 0.035), (0.0, 0.0), (99.0, 0.20), (-4.0, 0.0)):
        controller, _ = _controller(raw)
        assert abs(controller.get_forecast_markup() - expected) < 1e-9
