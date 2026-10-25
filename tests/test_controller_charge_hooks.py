"""Directe tests van de controller-haakjes voor de laadregeling."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.ev_planner.const import (
    NATIVE_ENERGY_NEEDED,
    NATIVE_MAX_PRICE,
    NATIVE_MIN_PV_KWH,
    PLANNER_MODE_NORMAL,
    PV_ROUNDING_DOWN,
)
from custom_components.ev_planner.core.ev_planner import (
    DesiredCharge,
    EVPlannerController,
)
from custom_components.ev_planner.core.logger import Logger

NOW = datetime(2026, 10, 1, 22, 30, tzinfo=timezone.utc)


def _controller(**attrs):
    controller = object.__new__(EVPlannerController)
    controller.logger = Logger()
    controller._plan_lock = threading.RLock()
    controller.last_plan = None
    controller.last_plan_time = None
    controller.published = []
    controller.__dict__.update(attrs)
    return controller


def _decision(start_min, end_min, *, selected=True, energy=2.0, phases=3, current=16):
    return SimpleNamespace(
        hour=SimpleNamespace(
            start=NOW + timedelta(minutes=start_min),
            end=NOW + timedelta(minutes=end_min),
        ),
        selected=selected,
        energy_kwh=energy,
        phases=phases,
        charge_current_a=current,
    )


##############################################################################
# _get_settings: override van de benodigde energie
##############################################################################


def _settings_controller(override):
    values = {
        NATIVE_ENERGY_NEEDED: 12.0,
        NATIVE_MAX_PRICE: 30.0,
        NATIVE_MIN_PV_KWH: 0.0,
    }
    states = {"time.dep": "07:00:00", "select.day": "Morgen"}
    return _controller(
        energy_override_kwh=override,
        entities={
            "energy_needed": "x",
            "max_price": "x",
            "min_pv_kwh": "x",
            "departure": "x",
            "departure_day": "x",
        },
        hass=SimpleNamespace(get_state=lambda entity: states.get(entity)),
        _native_number_state=lambda key, entity, default: values.get(key, default),
        _native_entity_id=lambda domain, native, entity: {
            "time": "time.dep",
            "select": "select.day",
        }[domain],
        _get_planner_mode=lambda: PLANNER_MODE_NORMAL,
        _get_pv_rounding=lambda: PV_ROUNDING_DOWN,
        _get_max_phase_switches=lambda: 2,
        _get_max_pauses=lambda: 1,
        _get_max_charge_power_kw=lambda: 11.04,
    )


def test_settings_use_the_entity_value_without_override():
    settings = _settings_controller(None)._get_settings(NOW)
    assert settings.energy_needed_kwh == 12.0
    assert settings.max_pauses == 1  # de pauzelimiet bereikt de planner


def test_settings_use_the_override_when_set():
    assert _settings_controller(5.5)._get_settings(NOW).energy_needed_kwh == 5.5


def test_zero_override_is_not_a_valid_plan_target():
    assert _settings_controller(0.0)._get_settings(NOW) is None


##############################################################################
# create_plan: doel bereikt
##############################################################################


class _Raises:
    def read(self):
        raise AssertionError("prijzen horen niet gelezen te worden")


def test_goal_reached_clears_the_plan_without_reading_prices():
    cleared = []
    controller = _controller(
        energy_override_kwh=0.0,
        enabled=True,
        last_plan=object(),
        last_plan_time=NOW,
        prices=_Raises(),
        solcast=_Raises(),
        scheduler=SimpleNamespace(clear_plan=lambda: cleared.append(True)),
        _is_enabled=lambda: True,
        _publish_plan_data=lambda: None,
    )
    controller._publish_planner_state = controller.published.append

    assert controller.create_plan(NOW) is None

    assert cleared == [True]
    assert controller.last_plan is None
    assert controller.published == ["Doel bereikt"]


##############################################################################
# Alleen-lezen koppeling
##############################################################################


def _plan_controller(allowed=True):
    plan = SimpleNamespace(
        decisions=[
            _decision(-30, 30, energy=3.0),
            _decision(60, 90, selected=False),
            _decision(120, 180, energy=4.0, phases=1, current=12),
        ]
    )
    return _controller(
        last_plan=plan,
        scheduler=SimpleNamespace(charging_allowed=lambda now: allowed),
    )


def test_desired_charge_follows_the_active_decision():
    assert _plan_controller().get_desired_charge(NOW) == DesiredCharge(True, 3, 16)
    later = NOW + timedelta(minutes=150)
    assert _plan_controller().get_desired_charge(later) == DesiredCharge(True, 1, 12)


def test_desired_charge_is_off_without_decision_or_permission():
    desired = _plan_controller(allowed=False).get_desired_charge(NOW)
    assert desired == DesiredCharge(False, 0, 0)
    gap = NOW + timedelta(minutes=45)
    assert _plan_controller().get_desired_charge(gap) == DesiredCharge(False, 0, 0)
    assert _controller(
        scheduler=SimpleNamespace(charging_allowed=lambda now: True)
    ).get_desired_charge(NOW) == DesiredCharge(False, 0, 0)


def test_plan_decisions_only_contain_selected_windows():
    decisions = _plan_controller().get_plan_decisions()
    assert [round(d[2], 1) for d in decisions] == [3.0, 4.0]
    assert _controller().get_plan_decisions() == []


##############################################################################
# replan_keep_previous
##############################################################################


def _replan_controller(new_plan, override=None):
    previous = object()
    restored = {}

    def replan(now=None):
        controller.last_plan = new_plan
        controller.last_plan_time = NOW if new_plan else None
        return new_plan

    controller = _controller(
        energy_override_kwh=override,
        last_plan=previous,
        last_plan_time=NOW - timedelta(hours=1),
        scheduler=SimpleNamespace(
            set_plan=lambda plan: restored.setdefault("plan", plan)
        ),
        replan=replan,
        _publish_plan_data=lambda: restored.setdefault("published", True),
    )
    return controller, previous, restored


def test_failed_replan_restores_the_previous_plan():
    controller, previous, restored = _replan_controller(None)
    assert controller.replan_keep_previous(NOW) is None
    assert controller.last_plan is previous
    assert restored["plan"] is previous and restored["published"]


def test_successful_replan_keeps_the_new_plan():
    new = object()
    controller, previous, restored = _replan_controller(new)
    assert controller.replan_keep_previous(NOW) is new
    assert controller.last_plan is new
    assert restored == {}


def test_goal_reached_is_not_a_failed_replan():
    controller, previous, restored = _replan_controller(None, override=0.0)
    assert controller.replan_keep_previous(NOW) is None
    assert controller.last_plan is None
    assert restored == {}


##############################################################################
# Vertrekdatum bij automatische herplanning
##############################################################################


def _planned_controller():
    controller = _settings_controller(None)
    first = controller._get_settings(NOW)
    controller.plan_departure = first.departure_time  # zoals create_plan doet
    controller._departure_inputs = controller._pending_departure_inputs
    return controller, first.departure_time


def test_automatic_replan_keeps_the_original_departure_date():
    controller, departure = _planned_controller()
    assert departure == datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)

    on_departure_day = datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)

    controller._keep_departure = True
    assert controller._get_settings(on_departure_day).departure_time == departure


def test_user_replan_uses_the_entity_settings():
    controller, departure = _planned_controller()
    on_departure_day = datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)

    # niet-automatisch: "Morgen" betekent weer de dag erna
    assert controller._get_settings(on_departure_day).departure_time == (
        datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)
    )


def test_changed_departure_time_or_day_overrides_the_kept_date():
    controller, _ = _planned_controller()
    controller._keep_departure = True
    on_departure_day = datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)

    controller.hass = SimpleNamespace(
        get_state=lambda entity: {
            "time.dep": "08:30:00",
            "select.day": "Morgen",
        }.get(entity)
    )
    assert controller._get_settings(on_departure_day).departure_time == (
        datetime(2026, 10, 3, 8, 30, tzinfo=timezone.utc)
    )


def test_kept_departure_expires_once_it_has_passed():
    controller, _ = _planned_controller()
    controller._keep_departure = True
    after = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)

    assert controller._get_settings(after).departure_time == (
        datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)
    )


def test_replan_keep_previous_sets_and_clears_the_keep_flag():
    controller = _controller()
    seen = []
    controller.replan = lambda now=None: seen.append(controller._keep_departure)
    controller.energy_override_kwh = None
    controller.replan_keep_previous(NOW)
    assert seen == [True] and controller._keep_departure is False


def test_departure_anchor_roundtrip_survives_a_restart():
    controller, departure = _planned_controller()
    saved = controller.departure_anchor_data()

    fresh = _settings_controller(None)
    fresh.restore_departure_anchor(saved)
    fresh._keep_departure = True

    on_departure_day = datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)
    assert fresh._get_settings(on_departure_day).departure_time == departure


def test_restore_ignores_junk_and_empty_data():
    for junk in (
        None,
        {},
        {"departure": "x"},
        {
            "departure": "2026-10-02T07:00:00",
            "clock": "07:00",
            "day": "Morgen",
        },
    ):
        controller = _controller()
        controller.restore_departure_anchor(junk)
        assert controller.plan_departure is None
    assert _controller().departure_anchor_data() == {}
