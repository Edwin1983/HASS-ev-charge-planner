"""Differentiele test: snelle numpy-DP tegenover de oorspronkelijke DP."""

from __future__ import annotations

import copy
import random
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.ev_planner.core import planner_fast
from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings


def _case(rnd: random.Random):
    count = rnd.choice([6, 8, 12, 16, 24])
    step = rnd.choice([15, 15, 60])
    start = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
    hours = []
    for index in range(count):
        begin = start + timedelta(minutes=step * index)
        hours.append(
            Hour(
                start=begin,
                end=begin + timedelta(minutes=step),
                price=round(rnd.uniform(-0.05, 0.40), rnd.choice([2, 4])),
                pv_estimate=rnd.choice([0.0, 0.0, rnd.uniform(0, 2.0)]),
            )
        )
    if rnd.random() < 0.7:
        hours[0].start += timedelta(seconds=rnd.uniform(1, step * 60 - 60))
    if rnd.random() < 0.4:
        hours[-1].end -= timedelta(seconds=rnd.uniform(60, step * 60 - 60))

    max_price = rnd.choice([1.0, 1.0, 0.30])
    capacity = sum(
        (hour.end - hour.start).total_seconds() / 3600.0 * 11.04
        for hour in hours
        if hour.price <= max_price or hour.pv_estimate > 0
    )
    target = max(0.5, round(rnd.uniform(0.15, 0.6) * min(capacity, 40), 2))
    return hours, target, max_price, rnd.choice([0, 1, 2, 3, 8])


def _plan(hours, target, max_price, switches, use_fast):
    settings = PlannerSettings(
        energy_needed_kwh=target,
        departure_time=datetime(2026, 10, 3, tzinfo=timezone.utc),
        max_price=max_price,
        max_phase_switches=switches,
    )
    planner = EVPlanner(PriceData(), SolcastData(), settings, Logger())
    original = planner_fast.available
    planner_fast.available = lambda: use_fast
    try:
        planner._optimize_hours(hours)
    finally:
        planner_fast.available = original
    return hours


def _cost(hours):
    return sum(h.paid_energy * h.price for h in hours if h.selected)


def _energy(hours):
    return sum(h.charge_energy for h in hours if h.selected)


def test_fast_planner_matches_reference_dp():
    if not planner_fast.available():
        pytest.skip("numpy niet beschikbaar")

    rnd = random.Random(20261001)
    compared = 0

    for _ in range(40):
        hours, target, max_price, switches = _case(rnd)
        reference = _plan(copy.deepcopy(hours), target, max_price, switches, False)
        if abs(_energy(reference) - target) > 1e-5:
            continue  # onhaalbaar: de snelle route valt dan bewust terug

        fast = _plan(copy.deepcopy(hours), target, max_price, switches, True)

        assert abs(_energy(fast) - target) < 1e-5
        assert abs(_cost(fast) - _cost(reference)) < 2e-6

        phases = [h.phases for h in sorted(fast, key=lambda h: h.start) if h.selected]
        assert sum(a != b for a, b in zip(phases, phases[1:])) <= switches
        compared += 1

    assert compared >= 15
