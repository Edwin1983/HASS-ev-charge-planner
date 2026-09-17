from pathlib import Path

root = Path(__file__).resolve().parents[1]

path = root / "tests/test_integration.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "    CONF_ENTITY_PLANNER_MODE,\n",
    "    CONF_ENTITY_PLANNER_MODE,\n    CONF_ENTITY_PV_ROUNDING,\n",
    1,
)
text = text.replace(
    '        CONF_ENTITY_PLANNER_MODE: "input_select.ev_planner_mode",\n',
    '        CONF_ENTITY_PLANNER_MODE: "input_select.ev_planner_mode",\n        CONF_ENTITY_PV_ROUNDING: "input_select.ev_pv_afronding",\n',
    1,
)
text = text.replace(
    '    hass.states.async_set("input_select.ev_planner_mode", "Auto")\n',
    '    hass.states.async_set("input_select.ev_planner_mode", "Normaal laden")\n    hass.states.async_set("input_select.ev_pv_afronding", "Naar beneden — geen netenergie")\n',
    1,
)
path.write_text(text, encoding="utf-8")

solar_tests = '''from datetime import datetime, timedelta, timezone

from custom_components.ev_planner.const import (
    PLANNER_MODE_NORMAL,
    PLANNER_MODE_SOLAR_ONLY,
    PV_ROUNDING_DOWN,
    PV_ROUNDING_UP,
)
from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings


def make_planner(pv_kwh, mode, rounding, needed, price=0.40, max_price=0.0, min_pv=0.0):
    start = datetime.now(timezone.utc) + timedelta(minutes=2)
    start = start.replace(second=0, microsecond=0)
    end = start + timedelta(hours=1)
    hour = Hour(start=start, end=end, price=price)
    hour.pv_estimate = pv_kwh
    hour.pv_estimate10 = pv_kwh
    hour.pv_estimate90 = pv_kwh
    settings = PlannerSettings(
        energy_needed_kwh=needed,
        departure_time=end,
        max_price=max_price,
        min_pv_kwh=min_pv,
        solar_is_free=True,
        max_charge_power_kw=11.04,
        max_phase_switches=8,
        planner_mode=mode,
        pv_rounding=rounding,
    )
    return EVPlanner(PriceData(hours=[hour]), SolcastData(hours=[hour]), settings, Logger())


def test_solar_down_2100w_1ph_is_9a():
    plan = make_planner(2.1, PLANNER_MODE_SOLAR_ONLY, PV_ROUNDING_DOWN, 2.07).create_plan()
    decision = plan.decisions[0]
    assert decision.charge_current_a == 9
    assert decision.phases == 1
    assert abs(decision.charge_power_kw - 2.07) < 0.000001
    assert decision.paid_energy_kwh == 0.0


def test_solar_up_2100w_1ph_is_10a_with_grid_supplement():
    plan = make_planner(2.1, PLANNER_MODE_SOLAR_ONLY, PV_ROUNDING_UP, 2.3).create_plan()
    decision = plan.decisions[0]
    assert decision.charge_current_a == 10
    assert decision.phases == 1
    assert abs(decision.charge_power_kw - 2.3) < 0.000001
    assert abs(decision.free_energy_kwh - 2.1) < 0.000001
    assert abs(decision.paid_energy_kwh - 0.2) < 0.000001


def test_solar_rounding_grid_is_not_blocked_by_max_price():
    plan = make_planner(2.1, PLANNER_MODE_SOLAR_ONLY, PV_ROUNDING_UP, 2.3, price=0.40, max_price=0.0).create_plan()
    assert plan.complete
    assert abs(plan.paid_energy_kwh - 0.2) < 0.000001


def test_min_pv_filters_solar_only():
    plan = make_planner(0.5, PLANNER_MODE_SOLAR_ONLY, PV_ROUNDING_DOWN, 1.38, min_pv=1.0).create_plan()
    assert not plan.decisions
    assert plan.paid_energy_kwh == 0.0


def test_min_pv_is_ignored_in_normal_mode():
    plan = make_planner(0.5, PLANNER_MODE_NORMAL, PV_ROUNDING_DOWN, 1.38, price=0.0, max_price=0.0, min_pv=1.0).create_plan()
    assert plan.complete
    assert plan.free_energy_kwh > 0.0


def test_normal_max_price_zero_blocks_grid_energy():
    plan = make_planner(0.0, PLANNER_MODE_NORMAL, PV_ROUNDING_DOWN, 1.38, price=0.10, max_price=0.0).create_plan()
    assert plan.paid_energy_kwh == 0.0
    assert not plan.complete
'''
(root / "tests/test_solar_planner.py").write_text(solar_tests, encoding="utf-8")
