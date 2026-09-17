from datetime import datetime, timedelta, timezone

from custom_components.ev_planner.const import (
    PLANNER_MODE_NORMAL,
    PLANNER_MODE_SOLAR_ONLY,
    PV_ROUNDING_DOWN,
    PV_ROUNDING_UP,
)
from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings


def build_planner(
    pv_values,
    needed,
    rounding=PV_ROUNDING_DOWN,
    mode=PLANNER_MODE_SOLAR_ONLY,
    max_price=0.0,
    price=0.40,
    min_pv=0.0,
    max_phase_switches=8,
):
    start = datetime.now(timezone.utc) + timedelta(minutes=2)
    start = start.replace(second=0, microsecond=0)
    hours = []
    for index, pv_kwh in enumerate(pv_values):
        hour_start = start + timedelta(hours=index)
        hour_end = hour_start + timedelta(hours=1)
        hour = Hour(start=hour_start, end=hour_end, price=price)
        hour.pv_estimate = pv_kwh
        hour.pv_estimate10 = pv_kwh
        hour.pv_estimate90 = pv_kwh
        hour.usable_pv = pv_kwh
        hours.append(hour)

    settings = PlannerSettings(
        energy_needed_kwh=needed,
        departure_time=hours[-1].end,
        max_price=max_price,
        min_pv_kwh=min_pv,
        solar_is_free=True,
        max_charge_power_kw=11.04,
        max_phase_switches=max_phase_switches,
        planner_mode=mode,
        pv_rounding=rounding,
    )
    return EVPlanner(
        PriceData(hours=hours),
        SolcastData(hours=hours),
        settings,
        Logger(),
    )


def test_solar_down_2100w_uses_9a_without_grid_energy():
    plan = build_planner([2.1], 2.07, rounding=PV_ROUNDING_DOWN).create_plan()
    decision = plan.decisions[0]

    assert plan.complete
    assert decision.charge_current_a == 9
    assert decision.phases == 1
    assert decision.charge_power_kw == 2.07
    assert decision.free_energy_kwh == 2.07
    assert decision.paid_energy_kwh == 0.0


def test_solar_up_2100w_uses_10a_and_allows_small_grid_supplement():
    plan = build_planner([2.1], 2.3, rounding=PV_ROUNDING_UP).create_plan()
    decision = plan.decisions[0]

    assert plan.complete
    assert decision.charge_current_a == 10
    assert decision.phases == 1
    assert abs(decision.charge_power_kw - 2.3) < 0.000001
    assert abs(decision.free_energy_kwh - 2.1) < 0.000001
    assert abs(decision.paid_energy_kwh - 0.2) < 0.000001


def test_solar_rounding_up_is_not_blocked_by_max_price_zero():
    plan = build_planner(
        [2.1],
        2.3,
        rounding=PV_ROUNDING_UP,
        max_price=0.0,
        price=0.40,
    ).create_plan()

    assert plan.complete
    assert abs(plan.paid_energy_kwh - 0.2) < 0.000001


def test_solar_rounding_down_below_6a_does_not_start_charging():
    plan = build_planner([1.0], 1.0, rounding=PV_ROUNDING_DOWN).create_plan()

    assert plan.decisions == []
    assert plan.energy_planned_kwh == 0.0
    assert plan.paid_energy_kwh == 0.0
    assert not plan.complete


def test_solar_rounding_up_below_6a_uses_6a():
    plan = build_planner([1.0], 1.38, rounding=PV_ROUNDING_UP).create_plan()
    decision = plan.decisions[0]

    assert decision.charge_current_a == 6
    assert decision.phases == 1
    assert abs(decision.charge_power_kw - 1.38) < 0.000001
    assert abs(decision.free_energy_kwh - 1.0) < 0.000001
    assert abs(decision.paid_energy_kwh - 0.38) < 0.000001


def test_solar_uses_3ph_when_pv_exceeds_1ph_capacity():
    plan = build_planner([5.5], 4.83, rounding=PV_ROUNDING_DOWN).create_plan()
    decision = plan.decisions[0]

    assert decision.phases == 3
    assert decision.charge_current_a == 7
    assert abs(decision.charge_power_kw - 4.83) < 0.000001
    assert decision.paid_energy_kwh == 0.0


def test_solar_rounding_up_can_reach_5_52kw_with_8a_3ph():
    plan = build_planner([5.5], 5.52, rounding=PV_ROUNDING_UP).create_plan()
    decision = plan.decisions[0]

    assert decision.phases == 3
    assert decision.charge_current_a == 8
    assert abs(decision.charge_power_kw - 5.52) < 0.000001
    assert abs(decision.paid_energy_kwh - 0.02) < 0.000001


def test_solar_never_exceeds_16a_3ph_maximum():
    plan = build_planner([15.0], 11.04, rounding=PV_ROUNDING_UP).create_plan()
    decision = plan.decisions[0]

    assert decision.phases == 3
    assert decision.charge_current_a == 16
    assert abs(decision.charge_power_kw - 11.04) < 0.000001
    assert abs(decision.energy_kwh - 11.04) < 0.000001
    assert decision.paid_energy_kwh == 0.0


def test_solar_min_pv_filters_only_solar_mode():
    solar_plan = build_planner(
        [0.5],
        1.38,
        rounding=PV_ROUNDING_DOWN,
        min_pv=1.0,
        mode=PLANNER_MODE_SOLAR_ONLY,
    ).create_plan()
    normal_plan = build_planner(
        [0.5],
        1.38,
        rounding=PV_ROUNDING_DOWN,
        min_pv=1.0,
        mode=PLANNER_MODE_NORMAL,
        max_price=0.0,
        price=0.0,
    ).create_plan()

    assert solar_plan.decisions == []
    assert normal_plan.energy_planned_kwh > 0.0


def test_solar_can_finish_partway_through_final_hour():
    plan = build_planner([4.6], 2.76, rounding=PV_ROUNDING_DOWN).create_plan()
    decision = plan.decisions[0]

    assert plan.complete
    assert abs(decision.energy_kwh - 2.76) < 0.000001
    assert decision.hour.end < decision.hour.original_end


def test_solar_gap_resets_phase_switch_counter():
    plan = build_planner(
        [5.52, 0.0, 5.52],
        11.04,
        rounding=PV_ROUNDING_DOWN,
        max_phase_switches=0,
    ).create_plan()

    assert plan.complete
    assert len(plan.decisions) == 2
    assert plan.decisions[0].phases == 3
    assert plan.decisions[1].phases == 3


def test_solar_zero_pv_never_creates_grid_only_charging():
    plan = build_planner(
        [0.0, 0.0],
        2.0,
        rounding=PV_ROUNDING_UP,
        max_price=0.0,
        price=0.0,
    ).create_plan()

    assert plan.decisions == []
    assert plan.energy_planned_kwh == 0.0
    assert plan.paid_energy_kwh == 0.0
    assert not plan.complete


def test_solar_energy_accounting_is_consistent():
    plan = build_planner([2.1], 2.3, rounding=PV_ROUNDING_UP).create_plan()

    assert abs(
        plan.free_energy_kwh
        + plan.paid_energy_kwh
        - plan.energy_planned_kwh
    ) < 0.000001
