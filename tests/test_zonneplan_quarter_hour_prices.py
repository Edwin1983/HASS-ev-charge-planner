"""Tests for Zonneplan quarter-hour electricity prices."""

from datetime import datetime, timedelta

from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import (
    Hour,
    PriceData,
    SolcastData,
)
from custom_components.ev_planner.core.prices import PriceReader
from custom_components.ev_planner.core.solcast import SolcastReader


def test_zonneplan_quarter_hour_format_is_parsed():
    attributes = {
        "forecast": [
            {
                "start_date": "2026-09-25T16:00:00+02:00",
                "end_date": "2026-09-25T16:15:00+02:00",
                "price_tax_included": {"amount": 2000000},
            }
        ]
    }
    reader = PriceReader(DummyApp(attributes), Logger(), "sensor.zonneplan")
    hours = reader._parse_forecast(attributes["forecast"])
    assert len(hours) == 1
    assert hours[0].start.hour == 16
    assert hours[0].start.minute == 0
    assert hours[0].end.minute == 15
    assert hours[0].price == 0.2


def test_legacy_hourly_format_is_still_parsed():
    attributes = {
        "forecast": [
            {
                "start_date": "2026-09-25T16:00:00+02:00",
                "electricity_price": 2000000,
                "sustainability_score": {"permille": 900},
            }
        ]
    }
    reader = PriceReader(DummyApp(attributes), Logger(), "sensor.zonneplan")
    hours = reader._parse_forecast(attributes["forecast"])
    assert len(hours) == 1
    assert hours[0].end.minute == 0
    assert hours[0].end.hour == 17
    assert hours[0].price == 0.2
    assert hours[0].sustainability_score == 900.0


def test_invalid_quarter_hour_end_date_is_rejected():
    attributes = {
        "forecast": [
            {
                "start_date": "2026-09-25T16:00:00+02:00",
                "end_date": "2026-09-25T15:45:00+02:00",
                "price_tax_included": {"amount": 2000000},
            }
        ]
    }
    reader = PriceReader(DummyApp(attributes), Logger(), "sensor.zonneplan")
    assert reader._parse_forecast(attributes["forecast"]) == []


def test_solcast_half_hour_record_has_correct_duration_and_energy():
    app = DummyApp(
        {
            "detailedForecast": [
                {
                    "period_start": "2026-09-25T16:00:00+02:00",
                    "pv_estimate": 4.0,
                    "pv_estimate10": 2.0,
                    "pv_estimate90": 6.0,
                }
            ]
        }
    )
    reader = SolcastReader(
        app,
        Logger(),
        "sensor.solcast",
        "sensor.solcast_tomorrow",
    )
    hours = reader._parse_forecast(app.attributes["detailedForecast"], 30)
    assert len(hours) == 1
    assert hours[0].end.hour == 16
    assert hours[0].end.minute == 30
    assert hours[0].pv_estimate == 2.0
    assert hours[0].pv_estimate10 == 1.0
    assert hours[0].pv_estimate90 == 3.0


def test_solcast_prefers_half_hourly_detailed_forecast():
    app = DummyApp(
        {
            "detailedForecast": [
                {
                    "period_start": "2026-09-25T16:00:00+02:00",
                    "pv_estimate": 4.0,
                }
            ],
            "detailedHourly": [
                {
                    "period_start": "2026-09-25T16:00:00+02:00",
                    "pv_estimate": 8.0,
                }
            ],
        }
    )
    reader = SolcastReader(app, Logger(), "sensor.solcast", "sensor.solcast_tomorrow")
    hours = reader._parse_forecast(app.attributes["detailedForecast"], 30)
    assert len(hours) == 1
    assert hours[0].end.minute == 30
    assert hours[0].pv_estimate == 2.0


def test_solcast_falls_back_to_hourly_detailed_forecast():
    app = DummyApp(
        {
            "detailedHourly": [
                {
                    "period_start": "2026-09-25T16:00:00+02:00",
                    "pv_estimate": 4.0,
                }
            ]
        }
    )
    reader = SolcastReader(
        app,
        Logger(),
        "sensor.solcast",
        "sensor.solcast_tomorrow",
    )
    hours = reader._parse_forecast(app.attributes["detailedHourly"], 60)
    assert len(hours) == 1
    assert hours[0].end.hour == 17
    assert hours[0].end.minute == 0
    assert hours[0].pv_estimate == 4.0


def test_half_hour_solcast_is_split_correctly_over_zonneplan_quarters():
    from custom_components.ev_planner.core.planner import (
        EVPlanner,
        PlannerSettings,
    )

    price_hours = [
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:00:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T16:15:00+02:00"
            ),
            price=0.10,
        ),
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:15:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T16:30:00+02:00"
            ),
            price=0.20,
        ),
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:30:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T16:45:00+02:00"
            ),
            price=0.30,
        ),
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:45:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T17:00:00+02:00"
            ),
            price=0.40,
        ),
    ]
    solar_hours = [
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:00:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T16:30:00+02:00"
            ),
            pv_estimate=2.0,
            pv_estimate10=1.0,
            pv_estimate90=3.0,
        ),
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:30:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T17:00:00+02:00"
            ),
            pv_estimate=1.0,
            pv_estimate10=0.5,
            pv_estimate90=1.5,
        ),
    ]

    planner = EVPlanner(
        PriceData(),
        SolcastData(),
        PlannerSettings(
            energy_needed_kwh=1.0,
            departure_time=datetime.fromisoformat(
                "2026-09-25T17:00:00+02:00"
            ),
            max_price=1.0,
        ),
        Logger(),
    )
    combined = []
    for index, price_hour in enumerate(price_hours):
        if index < 2:
            solar_hour = solar_hours[0]
        else:
            solar_hour = solar_hours[1]
        combined.append(
            planner._combine_hour_data(
                price_hour,
                solar_hour,
            )
        )

    assert [round(hour.pv_estimate, 6) for hour in combined] == [
        1.0,
        1.0,
        0.5,
        0.5,
    ]
    assert sum(hour.pv_estimate for hour in combined) == 3.0


def test_planner_selects_individual_zonneplan_quarters_by_price():
    from custom_components.ev_planner.core.planner import (
        EVPlanner,
        PlannerSettings,
    )

    start = (
        datetime.now()
        .astimezone()
        .replace(second=0, microsecond=0)
        + timedelta(hours=2)
    )
    price_hours = []
    solar_hours = []

    prices = [0.20, 0.40, 0.05, 0.30]
    for index, price in enumerate(prices):
        quarter_start = start + timedelta(minutes=15 * index)
        quarter_end = quarter_start + timedelta(minutes=15)
        price_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                price=price,
            )
        )
        solar_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                pv_estimate=0.0,
                pv_estimate10=0.0,
                pv_estimate90=0.0,
            )
        )

    settings = PlannerSettings(
        energy_needed_kwh=1.38,
        departure_time=start + timedelta(hours=1),
        max_price=1.0,
        solar_is_free=False,
        max_charge_power_kw=3.68,
        max_phase_switches=8,
    )
    planner = EVPlanner(
        PriceData(hours=price_hours),
        SolcastData(hours=solar_hours),
        settings,
        Logger(),
    )
    plan = planner.create_plan()

    selected = [decision for decision in plan.decisions if decision.selected]
    assert len(selected) == 2
    assert [decision.hour.start for decision in selected] == [
        start,
        start + timedelta(minutes=30),
    ]
    assert abs(plan.energy_planned_kwh - 1.38) < 0.000001


def test_scheduler_treats_selected_quarters_as_separate_runtime_windows():
    from custom_components.ev_planner.core.planner import (
        EVPlanner,
        PlannerSettings,
    )
    from custom_components.ev_planner.core.scheduler import (
        EVScheduler,
        SchedulerSettings,
    )

    start = (
        datetime.now()
        .astimezone()
        .replace(second=0, microsecond=0)
        + timedelta(hours=2)
    )
    price_hours = []
    solar_hours = []

    prices = [0.30, 0.40, 0.10, 0.20]
    for index, price in enumerate(prices):
        quarter_start = start + timedelta(minutes=15 * index)
        quarter_end = quarter_start + timedelta(minutes=15)
        price_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                price=price,
            )
        )
        solar_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                pv_estimate=0.0,
                pv_estimate10=0.0,
                pv_estimate90=0.0,
            )
        )

    settings = PlannerSettings(
        energy_needed_kwh=1.38,
        departure_time=start + timedelta(hours=1),
        max_price=1.0,
        solar_is_free=False,
        max_charge_power_kw=3.68,
        max_phase_switches=8,
    )
    planner = EVPlanner(
        PriceData(hours=price_hours),
        SolcastData(hours=solar_hours),
        settings,
        Logger(),
    )
    plan = planner.create_plan()

    scheduler = EVScheduler(
        SchedulerSettings(),
        Logger(),
    )
    scheduler.set_plan(plan)

    assert scheduler.get_current_hour(start) is None
    assert scheduler.get_current_hour(start + timedelta(minutes=15)) is None

    current = scheduler.get_current_hour(start + timedelta(minutes=30))
    assert current is not None
    assert current.start == start + timedelta(minutes=30)

    current = scheduler.get_current_hour(start + timedelta(minutes=45))
    assert current is not None
    assert current.start == start + timedelta(minutes=45)

    assert scheduler.get_current_hour(start + timedelta(hours=1)) is None


def test_partial_intervals_scale_solcast_pv_proportionally():
    from custom_components.ev_planner.core.planner import (
        EVPlanner,
        PlannerSettings,
    )

    base = datetime.now().astimezone().replace(microsecond=0)
    departure = base + timedelta(minutes=60)

    source_hours = [
        Hour(
            start=base - timedelta(minutes=30),
            end=base + timedelta(minutes=30),
            price=0.20,
            pv_estimate=4.0,
            pv_estimate10=2.0,
            pv_estimate90=6.0,
        ),
        Hour(
            start=base + timedelta(minutes=30),
            end=base + timedelta(minutes=90),
            price=0.20,
            pv_estimate=3.0,
            pv_estimate10=1.5,
            pv_estimate90=4.5,
        ),
    ]

    planner = EVPlanner(
        PriceData(hours=source_hours),
        SolcastData(),
        PlannerSettings(
            energy_needed_kwh=1.0,
            departure_time=departure,
            max_price=1.0,
        ),
        Logger(),
    )

    filtered = planner._filter_available_time(source_hours)

    assert len(filtered) == 2
    assert filtered[0].start >= base
    assert filtered[0].end == base + timedelta(minutes=30)
    first_fraction = (
        (filtered[0].end - filtered[0].start).total_seconds()
        / (source_hours[0].end - source_hours[0].start).total_seconds()
    )
    assert abs(filtered[0].pv_estimate - 4.0 * first_fraction) < 0.000001
    assert abs(filtered[0].pv_estimate10 - 2.0 * first_fraction) < 0.000001
    assert abs(filtered[0].pv_estimate90 - 6.0 * first_fraction) < 0.000001

    assert filtered[1].start >= base + timedelta(minutes=30)
    assert filtered[1].end == departure
    second_fraction = (
        (filtered[1].end - filtered[1].start).total_seconds()
        / (source_hours[1].end - source_hours[1].start).total_seconds()
    )
    assert abs(filtered[1].pv_estimate - 3.0 * second_fraction) < 0.000001
    assert abs(filtered[1].pv_estimate10 - 1.5 * second_fraction) < 0.000001
    assert abs(filtered[1].pv_estimate90 - 4.5 * second_fraction) < 0.000001


class DummyApp:
    def __init__(self, attributes):
        self.attributes = attributes

    def get_attributes(self, entity_id):
        return self.attributes



def test_57kwh_quarter_hour_dp_regression():
    """Benchmark the quarter-hour dynamic-programming core directly."""
    import os
    import time

    if os.getenv("EV_PLANNER_RUN_PERFORMANCE") != "1":
        import pytest
        pytest.skip("Performance benchmark disabled")

    from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings

    start = (
        datetime.now().astimezone().replace(second=0, microsecond=0)
        + timedelta(minutes=15)
    )

    def create_planner_and_hours():
        price_hours = []
        solar_hours = []
        for index in range(60):
            quarter_start = start + timedelta(minutes=15 * index)
            quarter_end = quarter_start + timedelta(minutes=15)
            price = [0.18, 0.42, 0.27, 0.11][index % 4]
            price_hours.append(Hour(start=quarter_start, end=quarter_end, price=price))
            solar_hours.append(
                Hour(
                    start=quarter_start,
                    end=quarter_end,
                    pv_estimate=0.0,
                    pv_estimate10=0.0,
                    pv_estimate90=0.0,
                )
            )
        planner = EVPlanner(
            PriceData(hours=price_hours), SolcastData(hours=solar_hours),
            PlannerSettings(
                energy_needed_kwh=57.0,
                departure_time=start + timedelta(minutes=15 * 60),
                max_price=1.0, solar_is_free=False,
                max_charge_power_kw=11.04, max_phase_switches=8,
            ), Logger(),
        )
        return planner, price_hours

    planner, warmup_hours = create_planner_and_hours()
    planner._optimize_hours(warmup_hours)

    started = time.perf_counter()
    for _ in range(10):
        planner, hours = create_planner_and_hours()
        planner._optimize_hours(hours)
        planned = sum(hour.charge_energy for hour in hours)
        assert abs(planned - 57.0) < 0.000001
    elapsed = time.perf_counter() - started

    print(f"57 kWh / 60 quarter-hours DP runtime: total_10_runs={elapsed:.3f}s")

def test_57kwh_quarter_hour_planning_regression():
    """Benchmark the same realistic 57 kWh quarter-hour workload repeatedly.

    The benchmark is opt-in. One warm-up run is followed by ten timed runs.
    The median is the primary number because individual CI runner samples
    can vary substantially.
    """
    import os
    import statistics
    import time

    if os.getenv("EV_PLANNER_RUN_PERFORMANCE") != "1":
        import pytest

        pytest.skip("Performance benchmark disabled")

    from custom_components.ev_planner.core.planner import (
        EVPlanner,
        PlannerSettings,
    )

    start = (
        datetime.now()
        .astimezone()
        .replace(second=0, microsecond=0)
        + timedelta(minutes=15)
    )

    price_hours = []
    solar_hours = []

    for index in range(60):
        quarter_start = start + timedelta(minutes=15 * index)
        quarter_end = quarter_start + timedelta(minutes=15)

        price = [0.18, 0.42, 0.27, 0.11][index % 4]

        price_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                price=price,
            )
        )

        solar_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                pv_estimate=0.0,
                pv_estimate10=0.0,
                pv_estimate90=0.0,
            )
        )

    def create_planner():
        return EVPlanner(
            PriceData(hours=price_hours),
            SolcastData(hours=solar_hours),
            PlannerSettings(
                energy_needed_kwh=57.0,
                departure_time=start + timedelta(minutes=15 * 60),
                max_price=1.0,
                solar_is_free=False,
                max_charge_power_kw=11.04,
                max_phase_switches=8,
            ),
            Logger(),
        )

    # Warm up Python/import/cache effects before collecting measurements.
    warmup_plan = create_planner().create_plan()
    assert warmup_plan.complete

    if os.getenv("EV_PLANNER_RUN_PROFILE") == "1":
        import cProfile
        import pstats

        profiler = cProfile.Profile()
        profiler.enable()
        profile_plan = create_planner().create_plan()
        profiler.disable()
        assert profile_plan.complete

        print("\n--- planner cProfile (cumulative) ---")
        stats = pstats.Stats(profiler).sort_stats("cumulative")
        stats.print_stats(30)
        print("--- end planner cProfile ---\n")

    samples = []

    for _ in range(10):
        planner = create_planner()
        started = time.perf_counter()
        plan = planner.create_plan()
        samples.append(time.perf_counter() - started)

        assert plan.complete
        assert abs(plan.energy_planned_kwh - 57.0) < 0.000001
        assert plan.missing_energy_kwh < 0.000001

        selected = [decision for decision in plan.decisions if decision.selected]
        assert selected

        switches = 0
        previous_phase = None

        for decision in selected:
            if previous_phase is not None and decision.phases != previous_phase:
                switches += 1
            previous_phase = decision.phases

        assert switches <= 8

        for decision in selected:
            assert decision.hour.end <= planner.settings.departure_time
            assert decision.charge_current_a >= 6
            assert decision.charge_current_a <= 16

    print(
        "57 kWh / 60 quarter-hours planner runtime: "
        f"min={min(samples):.3f}s "
        f"median={statistics.median(samples):.3f}s "
        f"mean={statistics.mean(samples):.3f}s "
        f"max={max(samples):.3f}s"
    )


def test_array_dp_matches_exact_dp_across_representative_scenarios(monkeypatch):
    """Compare the array DP with the original exact DP on several workloads."""
    import copy

    import custom_components.ev_planner.core.planner as planner_module
    from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings

    def build_hours(prices, pv, minutes=15):
        start = datetime(2026, 9, 25, 0, 0).astimezone()
        hours = []
        for index, price in enumerate(prices):
            end = start + timedelta(minutes=minutes * (index + 1))
            item = Hour(
                start=start + timedelta(minutes=minutes * index),
                end=end,
                price=price,
                pv_estimate=pv[index] if pv else 0.0,
                pv_estimate10=0.0,
                pv_estimate90=0.0,
            )
            hours.append(item)
        return hours

    scenarios = [
        {
            "name": "basic_3phase",
            "prices": [0.30, 0.10, 0.40, 0.20, 0.05, 0.35, 0.15, 0.25],
            "pv": None,
            "target": 11.04,
            "max_power": 11.04,
            "max_price": 1.0,
            "solar_is_free": False,
            "switches": 8,
        },
        {
            "name": "one_phase_only",
            "prices": [0.30, 0.10, 0.40, 0.20, 0.05, 0.35, 0.15, 0.25],
            "pv": None,
            "target": 5.52,
            "max_power": 3.68,
            "max_price": 1.0,
            "solar_is_free": False,
            "switches": 0,
        },
        {
            "name": "pv_free_energy",
            "prices": [0.30, 0.40, 0.10, 0.20, 0.35, 0.05, 0.25, 0.15],
            "pv": [0.0, 2.0, 1.0, 0.0, 2.5, 0.0, 1.5, 0.0],
            "target": 8.0,
            "max_power": 11.04,
            "max_price": 1.0,
            "solar_is_free": True,
            "switches": 8,
        },
        {
            "name": "max_price_with_free_slots",
            "prices": [0.50, 0.50, 0.20, 0.50, 0.10, 0.50, 0.30, 0.50],
            "pv": [0.0, 2.76, 0.0, 2.76, 0.0, 2.76, 0.0, 2.76],
            "target": 11.04,
            "max_power": 11.04,
            "max_price": 0.25,
            "solar_is_free": True,
            "switches": 8,
        },
        {
            "name": "phase_switch_budget",
            "prices": [0.05, 0.50, 0.06, 0.50, 0.07, 0.50, 0.08, 0.50],
            "pv": None,
            "target": 5.52,
            "max_power": 11.04,
            "max_price": 1.0,
            "solar_is_free": False,
            "switches": 1,
        },
        {
            "name": "longer_horizon",
            "prices": [0.05, 0.40, 0.20, 0.30] * 6,
            "pv": [0.0, 0.5, 1.0, 0.0] * 6,
            "target": 20.0,
            "max_power": 11.04,
            "max_price": 1.0,
            "solar_is_free": True,
            "switches": 8,
        },
    ]

    def run_scenario(spec, fast_enabled):
        hours = build_hours(spec["prices"], spec["pv"])
        settings = PlannerSettings(
            energy_needed_kwh=spec["target"],
            departure_time=hours[-1].end,
            max_price=spec["max_price"],
            solar_is_free=spec["solar_is_free"],
            max_charge_power_kw=spec["max_power"],
            max_phase_switches=spec["switches"],
        )
        planner = EVPlanner(
            PriceData(hours=[]),
            SolcastData(hours=[]),
            settings,
            Logger(),
        )
        planner._prepare_hour = lambda hour: None
        original_available = planner_module.planner_fast.available
        planner_module.planner_fast.available = lambda: fast_enabled
        try:
            planner._optimize_hours(hours)
        finally:
            planner_module.planner_fast.available = original_available

        selected = [
            (
                hour.start,
                hour.end,
                int(hour.phases),
                int(hour.charge_current_a),
                round(hour.charge_energy, 9),
                round(hour.free_energy, 9),
                round(hour.paid_energy, 9),
            )
            for hour in hours
            if hour.selected
        ]
        total_cost = sum(
            hour.paid_energy * hour.price
            for hour in hours
            if hour.selected
        )
        return selected, round(total_cost, 9)

    for spec in scenarios:
        exact = run_scenario(spec, False)
        fast = run_scenario(spec, True)

        assert fast == exact, spec["name"]
