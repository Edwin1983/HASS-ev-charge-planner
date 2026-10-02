from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import (
    EVPlanner,
    PlannerSettings,
    ChargingPlan,
)
from custom_components.ev_planner.core.scheduler import (
    EVScheduler,
    SchedulerSettings,
)
from custom_components.ev_planner.core.status import EVStatusManager


class DummyLogger:
    def debug(self, text):
        pass

    def info(self, text):
        pass

    def warning(self, text):
        pass

    def error(self, text):
        pass


def make_hour(start, price=0.10, pv=0.0, index=0):
    return Hour(
        start=start,
        end=start + timedelta(hours=1),
        price=price,
        price_raw=price,
        pv_estimate=pv,
        hour_index=index,
    )


def make_plan(hour):
    hour.selected = True
    hour.charge_energy = 1.84
    hour.free_energy = 0.0
    hour.paid_energy = 1.84
    hour.charge_power_w = 3680.0
    hour.charge_current_a = 16
    hour.phases = 1
    hour.reason = "test"

    from custom_components.ev_planner.core.planner import ChargingDecision

    decision = ChargingDecision(
        hour=hour,
        energy_kwh=hour.charge_energy,
        free_energy_kwh=hour.free_energy,
        paid_energy_kwh=hour.paid_energy,
        price=hour.price,
        cost=hour.paid_energy * hour.price,
        selected=True,
        reason=hour.reason,
        charge_power_kw=3.68,
        charge_current_a=16,
        phases=1,
    )

    return ChargingPlan(
        decisions=[decision],
        energy_needed_kwh=hour.charge_energy,
        energy_planned_kwh=hour.charge_energy,
        missing_energy_kwh=0.0,
        free_energy_kwh=hour.free_energy,
        paid_energy_kwh=hour.paid_energy,
        estimated_cost=decision.cost,
        complete=True,
        departure_time=hour.end,
        max_price=hour.price,
    )


def test_planner_settings_reject_invalid_values():
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError):
        PlannerSettings(0, now + timedelta(hours=1), 0.10)

    with pytest.raises(ValueError):
        PlannerSettings(1, now + timedelta(hours=1), -0.01)

    with pytest.raises(ValueError):
        PlannerSettings(
            1,
            now + timedelta(hours=1),
            0.10,
            max_charge_power_kw=11.05,
        )

    with pytest.raises(ValueError):
        PlannerSettings(
            1,
            now + timedelta(hours=1),
            0.10,
            max_phase_switches=-1,
        )


def test_planner_max_price_zero_never_uses_expensive_grid_energy():
    tz = timezone.utc
    now = datetime.now(tz)
    start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    prices = []
    solar = []

    for index, price in enumerate((0.374, 0.0, 0.374, 0.0)):
        hour = make_hour(start + timedelta(hours=index), price=price, index=index)
        prices.append(hour)
        solar.append(
            make_hour(start + timedelta(hours=index), price=0.0, pv=0.0, index=index)
        )

    planner = EVPlanner(
        PriceData(hours=prices),
        SolcastData(hours=solar),
        PlannerSettings(
            energy_needed_kwh=2.0,
            departure_time=start + timedelta(hours=4),
            max_price=0.0,
            max_charge_power_kw=11.04,
            max_phase_switches=8,
        ),
        DummyLogger(),
    )

    plan = planner.create_plan()

    assert plan.complete
    assert plan.energy_planned_kwh <= plan.energy_needed_kwh + 1e-6

    for decision in plan.decisions:
        assert decision.price <= 0.0 + 1e-9
        assert decision.paid_energy_kwh >= 0.0


def test_planner_outputs_valid_electrical_settings():
    tz = timezone.utc
    now = datetime.now(tz)
    start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    prices = []
    solar = []

    for index in range(4):
        prices.append(
            make_hour(
                start + timedelta(hours=index),
                price=0.10 + index * 0.01,
                pv=0.0,
                index=index,
            )
        )
        solar.append(
            make_hour(
                start + timedelta(hours=index),
                pv=0.0,
                index=index,
            )
        )

    plan = EVPlanner(
        PriceData(hours=prices),
        SolcastData(hours=solar),
        PlannerSettings(
            energy_needed_kwh=4.0,
            departure_time=start + timedelta(hours=4),
            max_price=0.50,
            max_charge_power_kw=11.04,
            max_phase_switches=8,
        ),
        DummyLogger(),
    ).create_plan()

    assert plan.energy_planned_kwh <= plan.energy_needed_kwh + 1e-6

    for decision in plan.decisions:
        assert decision.selected is True
        assert decision.charge_current_a == int(decision.charge_current_a)
        assert 6 <= decision.charge_current_a <= 16
        assert decision.phases in (1, 3)
        assert decision.charge_power_kw > 0.0
        assert decision.charge_power_kw <= 11.04 + 1e-6


def test_scheduler_activates_selected_hour_only_inside_window():
    now = datetime.now(timezone.utc)
    hour = make_hour(now - timedelta(minutes=10), price=0.10)
    plan = make_plan(hour)

    scheduler = EVScheduler(SchedulerSettings(), DummyLogger())
    scheduler.set_plan(plan)

    assert scheduler.get_current_hour(now) is hour
    assert scheduler.charging_allowed(now) is True

    after = hour.end + timedelta(seconds=1)
    assert scheduler.get_current_hour(after) is None
    assert scheduler.charging_allowed(after) is False


def test_scheduler_copies_planner_charging_settings_without_recalculation():
    now = datetime.now(timezone.utc)
    hour = make_hour(now - timedelta(minutes=10))
    plan = make_plan(hour)

    scheduler = EVScheduler(SchedulerSettings(), DummyLogger())
    scheduler.set_plan(plan)
    scheduler.update(now)

    assert scheduler.status.charge_power_w == 3680.0
    assert scheduler.status.charge_current_a == 16.0
    assert scheduler.status.phases == 1


def test_status_manager_reflects_scheduler():
    now = datetime.now(timezone.utc)
    hour = make_hour(now - timedelta(minutes=10), price=0.20)
    plan = make_plan(hour)

    scheduler = EVScheduler(SchedulerSettings(), DummyLogger())
    scheduler.set_plan(plan)
    scheduler.update(now)
    manager = EVStatusManager(scheduler, DummyLogger())

    status = manager.update(now)

    assert status.active is True
    assert status.charging_allowed is True
    assert status.start == hour.start
    assert status.end == hour.end
    assert status.charge_current_a == 16.0
    assert status.phases == 1
    assert status.reason == "test"
