"""Negatieve prijzen en deelplannen bij een onhaalbaar doel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.ev_planner.core import planner_fast
from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings
from custom_components.ev_planner.core.prices import PriceReader

START = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


def _hours(prices, minutes):
    return [
        Hour(
            start=START + timedelta(minutes=minutes * index),
            end=START + timedelta(minutes=minutes * (index + 1)),
            price=price,
            pv_estimate=0.0,
        )
        for index, price in enumerate(prices)
    ]


def _plan(hours, target, max_price=1.0, use_fast=True):
    settings = PlannerSettings(
        energy_needed_kwh=target,
        departure_time=START + timedelta(days=1),
        max_price=max_price,
        max_phase_switches=8,
    )
    planner = EVPlanner(PriceData(), SolcastData(), settings, Logger())
    original = planner_fast.available
    planner_fast.available = lambda: use_fast
    try:
        planner._optimize_hours(hours)
    finally:
        planner_fast.available = original
    return hours


def _each_engine(check):
    for use_fast in (True, False):
        check(use_fast)


def test_only_hours_below_max_price_are_planned_when_target_is_too_high():
    prices = [0.10, 0.10, 0.50, 0.50, 0.10, 0.50, 0.10, 0.50]

    def check(use_fast):
        hours = _plan(
            _hours(prices, 60), target=100.0, max_price=0.20, use_fast=use_fast
        )
        selected = [i for i, hour in enumerate(hours) if hour.selected]
        assert selected == [0, 1, 4, 6]
        assert all(abs(hours[i].charge_energy - 11.04) < 1e-9 for i in selected)

    _each_engine(check)


def test_short_time_window_plans_everything_that_fits():
    def check(use_fast):
        hours = _plan(_hours([0.2, 0.2, 0.2, 0.2], 15), target=20.0, use_fast=use_fast)
        assert all(hour.selected for hour in hours)
        assert abs(sum(hour.charge_energy for hour in hours) - 11.04) < 1e-9

    _each_engine(check)


def test_nothing_chargeable_gives_empty_plan_without_error():
    def check(use_fast):
        hours = _plan(
            _hours([0.5] * 6, 60),
            target=10.0,
            max_price=0.20,
            use_fast=use_fast,
        )
        assert not any(hour.selected for hour in hours)

    _each_engine(check)


def test_negative_price_slot_is_used():
    def check(use_fast):
        hours = _plan(
            _hours([0.30, -0.10, 0.30, 0.30], 15),
            target=2.76,
            use_fast=use_fast,
        )
        assert [i for i, hour in enumerate(hours) if hour.selected] == [1]
        assert sum(h.paid_energy * h.price for h in hours if h.selected) < 0

    _each_engine(check)


class _App:
    def __init__(self, attributes, state):
        self._attributes = attributes
        self._state = state

    def get_attributes(self, entity_id):
        return self._attributes

    def get_state(self, entity_id, attribute=None):
        return self._state


def test_price_reader_keeps_negative_prices():
    forecast = [
        {
            "start_date": "2026-09-25T13:00:00+02:00",
            "end_date": "2026-09-25T13:15:00+02:00",
            "price_tax_included": {"amount": -500000},
        }
    ]
    reader = PriceReader(
        _App({"forecast": forecast}, "-0.05"), Logger(), "sensor.zonneplan"
    )

    hours = reader._parse_forecast(forecast)

    assert len(hours) == 1
    assert abs(hours[0].price - (-0.05)) < 1e-12
    assert abs(reader._current_price() - (-0.05)) < 1e-12
