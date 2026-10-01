from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings


class DummyLogger:
    def debug(self, text):
        pass

    def info(self, text):
        pass

    def warning(self, text):
        pass

    def error(self, text):
        pass


def _build_planner():
    start = datetime(2026, 9, 27, tzinfo=timezone.utc)
    prices = []
    solar = []

    for index in range(60):
        hour_start = start + timedelta(hours=index)
        price = (0.18, 0.42, 0.27, 0.11)[index % 4]
        prices.append(
            Hour(
                start=hour_start,
                end=hour_start + timedelta(hours=1),
                price=price,
                price_raw=price,
                hour_index=index,
            )
        )
        solar.append(
            Hour(
                start=hour_start,
                end=hour_start + timedelta(hours=1),
                price=price,
                price_raw=price,
                usable_pv=0.0,
                hour_index=index,
            )
        )

    settings = PlannerSettings(
        energy_needed_kwh=57.0,
        departure_time=start + timedelta(hours=60),
        max_price=1.0,
        min_pv_kwh=0.0,
        solar_is_free=True,
        max_charge_power_kw=11.04,
        max_phase_switches=8,
    )

    return EVPlanner(
        PriceData(hours=prices),
        SolcastData(hours=solar),
        settings,
        DummyLogger(),
    )


def test_production_planner_performance():
    """Benchmark the actual production EVPlanner.create_plan() path."""
    planner = _build_planner()

    # Warm up once so import/setup effects are excluded.
    plan = planner.create_plan()
    assert plan.complete
    assert plan.energy_planned_kwh == 57.0

    samples = []
    for _ in range(5):
        planner = _build_planner()
        started = time.perf_counter()
        plan = planner.create_plan()
        elapsed = time.perf_counter() - started
        samples.append(elapsed)

        assert plan.complete
        assert plan.energy_planned_kwh == 57.0

    print(
        "Production EVPlanner 57 kWh / 60 hours runtime: "
        f"min={min(samples):.3f}s "
        f"median={statistics.median(samples):.3f}s "
        f"mean={statistics.mean(samples):.3f}s "
        f"max={max(samples):.3f}s"
    )
