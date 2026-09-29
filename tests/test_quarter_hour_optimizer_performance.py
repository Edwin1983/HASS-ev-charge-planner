"""Performance and regression tests for the experimental quarter-hour DP optimizer."""

from datetime import datetime, timedelta

from custom_components.ev_planner.core.quarter_hour_optimizer import (
    QuarterHourOptimizer,
)


def _benchmark_slots():
    start = datetime(2026, 9, 27, 0, 0)
    prices = [0.18, 0.42, 0.27, 0.11]
    slots = []
    for index in range(60):
        begin = start + timedelta(minutes=15 * index)
        slots.append(
            type(
                "Slot",
                (),
                {
                    "start": begin,
                    "end": begin + timedelta(minutes=15),
                    "price": prices[index % 4],
                    "usable_pv": 0.0,
                },
            )()
        )
    return slots


def test_57kwh_quarter_hour_dp_regression():
    slots = _benchmark_slots()
    plan = QuarterHourOptimizer(
        energy_needed_kwh=57.0,
        max_price=1.0,
        max_charge_power_kw=11.04,
        max_phase_switches=8,
    ).optimize(slots)

    assert plan.complete
    assert abs(plan.energy_kwh - 57.0) < 0.000001
    assert plan.missing_energy_kwh == 0.0
    assert plan.phase_switches <= 8


def test_57kwh_dp_respects_price_limit():
    slots = _benchmark_slots()
    plan = QuarterHourOptimizer(
        energy_needed_kwh=57.0,
        max_price=0.25,
        max_charge_power_kw=11.04,
        max_phase_switches=8,
    ).optimize(slots)

    assert plan.complete
    assert abs(plan.energy_kwh - 57.0) < 0.000001
    assert all(
        action.paid_energy_kwh == 0.0
        or slots[action.index].price <= 0.25
        for action in plan.actions
    )


def test_57kwh_quarter_hour_dp_performance():
    import os
    import statistics
    import time

    if os.getenv("EV_PLANNER_RUN_PERFORMANCE") != "1":
        import pytest

        pytest.skip("Performance benchmark disabled")

    slots = _benchmark_slots()
    optimizer = QuarterHourOptimizer(
        energy_needed_kwh=57.0,
        max_price=1.0,
        max_charge_power_kw=11.04,
        max_phase_switches=8,
    )

    warmup = optimizer.optimize(slots)
    assert warmup.complete

    samples = []
    for _ in range(10):
        started = time.perf_counter()
        plan = optimizer.optimize(slots)
        samples.append(time.perf_counter() - started)
        assert plan.complete
        assert abs(plan.energy_kwh - 57.0) < 0.000001

    print(
        "57 kWh / 60 quarter-hours DP runtime: "
        f"min={min(samples):.3f}s "
        f"median={statistics.median(samples):.3f}s "
        f"mean={statistics.mean(samples):.3f}s "
        f"max={max(samples):.3f}s"
    )


def test_dp_memory_profile_scaling():
    import os
    import pytest

    if os.getenv("EV_PLANNER_RUN_MEMORY_PROFILE") != "1":
        pytest.skip("Memory profile disabled")

    # Scale progressively through the production-relevant range.
    # 80 kWh replaces the previous 100 kWh stress case to keep the profile
    # bounded while still testing a substantially larger request.
    cases = (
        (10.0, 48),
        (25.0, 96),
        (50.0, 112),
        (80.0, 112),
    )
    for energy_kwh, slot_count in cases:
        start = datetime(2026, 9, 27, 0, 0)
        slots = []
        for index in range(slot_count):
            begin = start + timedelta(minutes=15 * index)
            slots.append(
                type(
                    "Slot",
                    (),
                    {
                        "start": begin,
                        "end": begin + timedelta(minutes=15),
                        "price": 0.20,
                        "usable_pv": 0.0,
                    },
                )()
            )

        optimizer = QuarterHourOptimizer(
            energy_needed_kwh=energy_kwh,
            max_price=1.0,
            max_charge_power_kw=11.04,
            max_phase_switches=8,
        )
        plan = optimizer.optimize(slots)
        assert plan.complete

        peak_rss = max(
            item["rss_mb"] for item in optimizer.memory_profile
        )
        peak_current_rss = max(
            item["current_rss_mb"] for item in optimizer.memory_profile
        )
        peak_trace = max(
            item["tracemalloc_peak_mb"]
            for item in optimizer.memory_profile
        )
        peak_live_nodes = max(
            item["live_nodes"] for item in optimizer.memory_profile
        )
        peak_live_actions = max(
            item["live_actions"] for item in optimizer.memory_profile
        )
        max_frontier_entries = max(
            item["frontier_entries"] for item in optimizer.memory_profile
        )
        peak_nodes_created = max(
            item["nodes_created"] for item in optimizer.memory_profile
        )

        print(
            f"DP memory profile {energy_kwh:g} kWh / "
            f"{slot_count} quarter-hours: "
            f"peak_rss={peak_rss:.1f} MB, "
            f"peak_current_rss={peak_current_rss:.1f} MB, "
            f"peak_tracemalloc={peak_trace:.1f} MB, "
            f"max_frontier_entries={max_frontier_entries}, "
            f"peak_live_nodes={peak_live_nodes}, "
            f"peak_live_actions={peak_live_actions}, "
            f"nodes_created={peak_nodes_created}"
        )
