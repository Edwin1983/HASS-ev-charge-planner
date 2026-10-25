"""Tests voor de HA-koppeling (entiteiten als lader) en de orkestratie."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import NamedTuple

from custom_components.ev_planner.charge_manager import (
    ChargeManager,
    ChargerEntities,
    HAChargerPort,
    energy_kwh_from_state,
)
from custom_components.ev_planner.core.charge_control import ChargerState
from custom_components.ev_planner.core.energy_feedback import FeedbackConfig
from custom_components.ev_planner.core.logger import Logger

T0 = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


##############################################################################
# Nep-HA
##############################################################################


class FakeHass:
    def __init__(self):
        self.values = {}
        self.attrs = {}
        self.calls = []
        self.states = SimpleNamespace(get=self._get)
        self.services = SimpleNamespace(async_call=self._call)

    def set(self, entity_id, value, **attrs):
        self.values[entity_id] = value
        self.attrs[entity_id] = attrs

    def _get(self, entity_id):
        if entity_id not in self.values:
            return None
        return SimpleNamespace(
            state=self.values[entity_id], attributes=self.attrs.get(entity_id, {})
        )

    async def _call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _entities(**over):
    config = {
        "charger_mode_entity": "sensor.mode",
        "charger_current_entity": "number.current",
        "charger_start_entity": "switch.charging",
        "charger_stop_entity": "button.stop",
        "charger_phase_entity": "number.phase",
        "charger_energy_entity": "sensor.energy",
    }
    config.update(over)
    return ChargerEntities.from_config(config)


async def _no_sleep(seconds):
    return None


##############################################################################
# Lader uit entiteiten
##############################################################################


def test_config_without_required_entities_gives_no_charger():
    assert ChargerEntities.from_config({}) is None
    assert ChargerEntities.from_config({"charger_mode_entity": "sensor.m"}) is None


def test_read_maps_states_phases_and_current():
    hass = FakeHass()
    hass.set("sensor.mode", "connected_charging")
    hass.set("number.current", "15.6")
    hass.set("number.phase", "32.0")
    port = HAChargerPort(hass, _entities())

    state = port.read()
    assert state.available and state.connected and state.charging
    assert not state.startable
    assert state.current_a == 16 and state.phases == 1

    hass.set("number.phase", "0")
    assert port.read().phases == 3
    hass.set("number.phase", "17")
    assert port.read().phases is None  # onbekende waarde -> nooit raden


def test_read_unavailable_mode_means_unavailable_charger():
    hass = FakeHass()
    hass.set("sensor.mode", "unavailable")
    assert not HAChargerPort(hass, _entities()).read().available
    assert not HAChargerPort(FakeHass(), _entities()).read().available


def test_custom_phase_values_and_state_lists():
    hass = FakeHass()
    hass.set("sensor.mode", "plugged")
    hass.set("number.current", "6")
    hass.set("number.phase", "1")
    entities = _entities(
        charger_connected_states="plugged, charging",
        charger_startable_states="plugged",
        charger_phase_one_value=1,
        charger_phase_three_value=3,
    )
    state = HAChargerPort(hass, entities).read()
    assert state.connected and state.startable and not state.charging
    assert state.phases == 1


async def test_commands_call_the_right_services():
    hass = FakeHass()
    hass.set("switch.charging", "off")
    port = HAChargerPort(hass, _entities(), sleep=_no_sleep)

    await port.stop_charging()
    await port.set_current(12)
    await port.set_phases(1)
    await port.set_phases(3)
    await port.start_charging()

    assert hass.calls == [
        ("button", "press", {"entity_id": "button.stop"}),
        ("number", "set_value", {"entity_id": "number.current", "value": 12}),
        ("number", "set_value", {"entity_id": "number.phase", "value": 32.0}),
        ("number", "set_value", {"entity_id": "number.phase", "value": 0.0}),
        ("switch", "turn_on", {"entity_id": "switch.charging"}),
    ]


async def test_start_toggles_when_switch_is_already_on():
    hass = FakeHass()
    hass.set("switch.charging", "on")
    await HAChargerPort(hass, _entities(), sleep=_no_sleep).start_charging()
    assert [c[1] for c in hass.calls] == ["turn_off", "turn_on"]


async def test_stop_falls_back_to_the_start_switch():
    hass = FakeHass()
    entities = _entities(charger_stop_entity="")
    await HAChargerPort(hass, entities, sleep=_no_sleep).stop_charging()
    assert hass.calls == [("switch", "turn_off", {"entity_id": "switch.charging"})]


async def test_charger_without_start_switch_does_not_fail_on_start():
    hass = FakeHass()
    entities = _entities(charger_start_entity="")
    await HAChargerPort(hass, entities, sleep=_no_sleep).start_charging()
    assert hass.calls == []


def test_energy_sensor_in_wh_is_converted():
    assert energy_kwh_from_state(
        SimpleNamespace(
            state="1500", attributes={"unit_of_measurement": "Wh"}
        )
    ) == 1.5
    assert energy_kwh_from_state(SimpleNamespace(state="2.5", attributes={})) == 2.5
    assert energy_kwh_from_state(
        SimpleNamespace(state="unavailable", attributes={})
    ) is None
    assert energy_kwh_from_state(None) is None


##############################################################################
# Orkestratie
##############################################################################


class Wanted(NamedTuple):
    allowed: bool
    phases: int
    current_a: int


class FakeController:
    """Maakt een plan van blokken van 5 kWh per uur (120 min tussenruimte)."""

    def __init__(self):
        self.enabled = True
        self.energy_override_kwh = None
        self.last_plan = None
        self.last_plan_time = None
        self.entity_target = 10.0
        self.decisions = []
        self.calls = []
        self.wanted = Wanted(True, 3, 16)
        self.fail_update = False

    def get_entity_energy_needed(self):
        return self.entity_target

    def energy_needed_entity_id(self):
        return "number.energy_needed"

    entity_goal = None

    def get_entity_energy_target(self):
        return self.entity_target

    def get_entity_energy_goal(self):
        return self.entity_goal if self.entity_goal is not None else self.entity_target

    def _make_plan(self, now):
        remaining = (
            self.entity_target
            if self.energy_override_kwh is None
            else self.energy_override_kwh
        )
        if remaining <= 0:
            self.last_plan = None
            self.last_plan_time = None
            self.decisions = []
            return
        self.decisions = []
        start = now
        while remaining > 1e-9:
            energy = min(5.0, remaining)
            self.decisions.append((start, start + timedelta(minutes=60), energy))
            remaining -= energy
            start += timedelta(minutes=120)
        self.last_plan = object()
        self.last_plan_time = now

    def update(self, now):
        if self.fail_update:
            raise RuntimeError("boem")
        self.calls.append(("update", self.energy_override_kwh))
        if self.last_plan is None and not (
            self.energy_override_kwh is not None and self.energy_override_kwh <= 0
        ):
            self._make_plan(now)

    def replan_keep_previous(self, now):
        self.calls.append(("replan", self.energy_override_kwh))
        self._make_plan(now)

    def get_plan_decisions(self):
        return list(self.decisions)

    def get_desired_charge(self, now):
        if self.last_plan is None:
            return Wanted(False, 0, 0)
        return self.wanted


class FakeStore:
    def __init__(self, data=None):
        self.data = data

    async def async_load(self):
        return self.data

    def async_delay_save(self, func, delay):
        self.data = func()

    async def async_save(self, data):
        self.data = data


class InstantCharger:
    def __init__(self):
        self.state = ChargerState(True, True, False, True, 0, 3)
        self.commands = []

    def read(self):
        return self.state

    async def stop_charging(self):
        self.commands.append("stop")
        self.state = ChargerState(
            True, True, False, True, self.state.current_a, self.state.phases
        )

    async def start_charging(self):
        self.commands.append("start")
        self.state = ChargerState(
            True, True, True, False, self.state.current_a, self.state.phases
        )

    async def set_current(self, amps):
        self.commands.append(f"current={amps}")
        self.state = ChargerState(
            True,
            True,
            self.state.charging,
            self.state.startable,
            amps,
            self.state.phases,
        )

    async def set_phases(self, phases):
        self.commands.append(f"phases={phases}")
        self.state = ChargerState(
            True,
            True,
            self.state.charging,
            self.state.startable,
            self.state.current_a,
            phases,
        )


def _logger():
    messages = []
    return (
        Logger(messages.append, messages.append, messages.append, messages.append),
        messages,
    )


def _manager(controller=None, charger=None, hass=None, store=None):
    hass = hass or FakeHass()
    hass.set("sensor.energy", "100.0", unit_of_measurement="kWh")
    controller = controller or FakeController()
    charger = charger or InstantCharger()
    logger, messages = _logger()
    manager = ChargeManager(
        hass,
        controller,
        _entities(),
        store=store or FakeStore(),
        logger=logger,
        feedback_config=FeedbackConfig(
            threshold_kwh=0.3,
            settle_s=60,
            min_replan_interval_s=300,
        ),
        port=charger,
        regulator_runtime={"sleep": _no_sleep},
    )
    manager._notify = lambda *a: None  # geen echte HA-meldingen
    manager.regulator._notify = lambda *a: None
    manager.regulator._clear_notification = lambda *a: None
    manager._started = False
    return manager, controller, charger, hass, messages


def _energy(hass, kwh):
    hass.set("sensor.energy", str(kwh), unit_of_measurement="kWh")


async def test_connecting_starts_session_and_plans_with_the_target():
    manager, ctl, charger, hass, _ = _manager()

    await manager._tick(at(0))

    assert manager.feedback.session_active
    assert manager.feedback.target_kwh == 10.0
    assert ("replan", 10.0) in ctl.calls
    assert charger.commands == []  # laadregeling staat standaard uit


async def test_shortfall_after_a_block_replans_with_remaining_energy():
    manager, ctl, charger, hass, _ = _manager()
    await manager._tick(at(0))

    _energy(hass, 102.0)
    await manager._tick(at(30))
    _energy(hass, 104.2)
    await manager._tick(at(60))
    await manager._tick(at(62))

    replans = [c for c in ctl.calls if c[0] == "replan"]
    assert replans[-1][1] is not None
    assert abs(replans[-1][1] - 5.8) < 1e-9
    assert manager.feedback.replans == 1


async def test_block_on_target_does_not_replan():
    manager, ctl, charger, hass, _ = _manager()
    await manager._tick(at(0))
    _energy(hass, 102.5)
    await manager._tick(at(30))
    _energy(hass, 105.0)
    await manager._tick(at(60))
    await manager._tick(at(62))

    assert [c for c in ctl.calls if c[0] == "replan"] == [("replan", 10.0)]


async def test_goal_reached_sets_override_zero_and_stops_planning():
    manager, ctl, charger, hass, _ = _manager()
    manager.toggles["enabled"] = True
    manager.toggles["dry_run"] = False
    await manager._tick(at(0))
    assert charger.commands  # laadregeling is gestart

    _energy(hass, 110.0)
    await manager._tick(at(30))

    assert ctl.last_plan is None
    assert not manager.feedback.session_active
    assert (
        "number",
        "set_value",
        {"entity_id": "number.energy_needed", "value": 0},
    ) in hass.calls
    assert "stop" in charger.commands or charger.state.charging is False


async def test_disconnect_ends_the_session():
    manager, ctl, charger, hass, _ = _manager()
    await manager._tick(at(0))
    assert manager.feedback.session_active

    charger.state = ChargerState(True, False, False, False, 0, 3)
    await manager._tick(at(10))

    assert not manager.feedback.session_active
    assert ctl.energy_override_kwh is None


async def test_changing_the_target_starts_a_new_session():
    manager, ctl, charger, hass, _ = _manager()
    await manager._tick(at(0))
    _energy(hass, 103.0)
    await manager._tick(at(10))

    ctl.entity_target = 6.0
    await manager._tick(at(11))

    assert manager.feedback.target_kwh == 6.0
    assert ctl.calls[-2][0] == "replan" and ctl.calls[-2][1] == 6.0


async def test_live_regulator_charges_when_planner_allows_it():
    manager, ctl, charger, hass, _ = _manager()
    manager.toggles["enabled"] = True
    manager.toggles["dry_run"] = False

    await manager._tick(at(0))

    assert charger.commands == ["current=16", "start"]


async def test_dry_run_never_touches_the_charger():
    manager, ctl, charger, hass, _ = _manager()
    manager.toggles["enabled"] = True  # dry_run blijft aan

    await manager._tick(at(0))

    assert charger.commands == []
    assert manager.regulator.status.mode == "dry-run"


async def test_smart_charging_off_releases_the_charger():
    manager, ctl, charger, hass, _ = _manager()
    manager.toggles["enabled"] = True
    manager.toggles["dry_run"] = False
    ctl.enabled = False

    await manager._tick(at(0))

    assert charger.commands == []
    assert manager.regulator.status.detail == "Smart Charging staat uit"


async def test_feedback_off_leaves_the_plan_alone():
    manager, ctl, charger, hass, _ = _manager()
    manager.toggles["feedback"] = False

    await manager._tick(at(0))
    _energy(hass, 101.0)
    await manager._tick(at(62))

    assert ctl.energy_override_kwh is None
    assert not manager.feedback.session_active
    assert all(call[0] == "update" for call in ctl.calls)


async def test_error_in_a_round_never_breaks_the_manager():
    manager, ctl, charger, hass, messages = _manager()
    ctl.fail_update = True

    await manager._tick(at(0))  # mag niet gooien

    assert any("onverwachte fout" in m for m in messages)
    ctl.fail_update = False
    await manager._tick(at(1))
    assert manager.feedback.session_active


async def test_session_survives_restart_via_store():
    store = FakeStore()
    manager, ctl, charger, hass, _ = _manager(store=store)
    await manager._tick(at(0))
    _energy(hass, 103.0)
    await manager._tick(at(30))
    manager.toggles["enabled"] = True
    await manager.async_stop()

    again, ctl2, charger2, hass2, _ = _manager(store=store)
    await again.async_load()

    assert again.toggles["enabled"] is True
    assert again.feedback.session_active
    assert abs(again.feedback.remaining_kwh() - 7.0) < 1e-9
    # Nog steeds dezelfde sessie: GEEN nieuwe sessie met het volledige doel.
    _energy(hass2, 103.0)
    await again._tick(at(40))
    assert abs(again.feedback.remaining_kwh() - 7.0) < 1e-9


async def test_extra_round_is_scheduled_at_the_next_plan_boundary():
    import sys
    import types

    scheduled = []

    def fake_track(hass, action, when):
        scheduled.append(when)
        return lambda: scheduled.append("cancelled")

    event_module = types.ModuleType("homeassistant.helpers.event")
    event_module.async_track_point_in_utc_time = fake_track
    sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    sys.modules.setdefault(
        "homeassistant.helpers", types.ModuleType("homeassistant.helpers")
    )
    previous = sys.modules.get("homeassistant.helpers.event")
    sys.modules["homeassistant.helpers.event"] = event_module

    try:
        manager, ctl, charger, hass, _ = _manager()
        manager._started = True
        ctl.get_plan_decisions = lambda: [
            (at(-15), at(15), 2.0),
            (at(30), at(45), 2.0),
        ]

        manager._schedule_boundary(at(0))
        assert scheduled == [(at(15) + timedelta(seconds=1)).astimezone(timezone.utc)]

        # opnieuw plannen annuleert de vorige
        manager._schedule_boundary(at(20))
        assert "cancelled" in scheduled
        assert scheduled[-1] == (at(30) + timedelta(seconds=1)).astimezone(timezone.utc)
    finally:
        if previous is None:
            sys.modules.pop("homeassistant.helpers.event", None)
        else:
            sys.modules["homeassistant.helpers.event"] = previous


async def test_falling_behind_during_a_block_replans_before_the_block_ends():
    manager, ctl, charger, hass, _ = _manager()
    await manager._tick(at(0))  # plan: 5 kWh in het uur van minuut 0 tot 60

    # 20 minuten later pas 0,5 kWh geleverd i.p.v. ~1,7
    _energy(hass, 100.5)
    await manager._tick(at(20))
    _energy(hass, 100.55)
    await manager._tick(at(21))

    replans = [c for c in ctl.calls if c[0] == "replan"]
    assert replans[-1][1] is not None
    assert abs(replans[-1][1] - 9.45) < 1e-6  # doel 10 - 0,55 geleverd
    assert manager.feedback.replans == 1


async def test_progress_threshold_follows_the_setting():
    manager, ctl, charger, hass, _ = _manager()
    ctl.get_progress_threshold_kwh = lambda: 1.5
    await manager._tick(at(0))

    # 0,6 kWh achter: onder de ingestelde drempel van 1,5 kWh, dus geen herplanning
    _energy(hass, 101.1)
    await manager._tick(at(20))
    _energy(hass, 101.15)
    await manager._tick(at(21))

    assert manager.feedback.config.progress_threshold_kwh == 1.5
    assert manager.feedback.replans == 0


async def test_unreadable_threshold_keeps_the_current_one():
    manager, ctl, charger, hass, _ = _manager()

    def broken():
        raise RuntimeError("weg")

    ctl.get_progress_threshold_kwh = broken
    before = manager.feedback.config.progress_threshold_kwh
    await manager._tick(at(0))
    assert manager.feedback.config.progress_threshold_kwh == before


async def test_reset_session_zeroes_energy_needed_and_allows_same_value_again():
    manager, ctl, charger, hass, _ = _manager()
    await manager._tick(at(0))
    assert manager.feedback.session_active

    await manager.async_reset_session()

    assert not manager.feedback.session_active
    assert ctl.energy_override_kwh is None
    assert (
        "number",
        "set_value",
        {"entity_id": "number.energy_needed", "value": 0},
    ) in hass.calls

    # dezelfde waarde opnieuw instellen start weer een nieuwe sessie
    await manager._tick(at(5))
    assert manager.feedback.session_active


def _reset_calls(hass):
    return [c for c in hass.calls if c[:2] == ("number", "set_value")]


async def test_minimum_reached_does_not_end_the_session_when_a_maximum_is_set():
    manager, ctl, charger, hass, _ = _manager()
    ctl.entity_goal = 14.0
    await manager._tick(at(0))
    assert manager.feedback.target_kwh == 10.0  # toetsing op het minimum

    _energy(hass, 110.0)  # minimum (10 kWh) geladen
    await manager._tick(at(30))

    assert manager.feedback.session_active
    assert ctl.energy_override_kwh is not None
    assert abs(ctl.energy_override_kwh - 4.0) < 1e-6
    assert abs(ctl.energy_delivered_kwh - 10.0) < 1e-6
    assert not _reset_calls(hass)


async def test_maximum_reached_ends_the_session_and_resets():
    manager, ctl, charger, hass, _ = _manager()
    ctl.entity_goal = 14.0
    await manager._tick(at(0))
    _energy(hass, 114.0)
    await manager._tick(at(40))

    assert not manager.feedback.session_active
    assert _reset_calls(hass)


async def test_feedback_only_compares_against_the_minimum_part_of_the_plan():
    manager, ctl, charger, hass, _ = _manager()
    ctl.entity_goal = 14.0
    ctl.decisions = [
        (at(0), at(60), 5.0),
        (at(120), at(180), 5.0),
        (at(240), at(300), 4.0),
    ]
    await manager._tick(at(0))
    blocks = manager._plan_blocks_for_feedback()
    assert [round(b[2], 6) for b in blocks] == [5.0, 5.0]


async def test_changing_only_the_maximum_replans_without_a_new_session():
    manager, ctl, charger, hass, _ = _manager()
    await manager._tick(at(0))
    started = manager.feedback.session_started_at
    replans_before = [c for c in ctl.calls if c[0] == "replan"]

    ctl.entity_goal = 16.0
    await manager._tick(at(5))

    assert manager.feedback.session_started_at == started
    assert manager._session_goal == 16.0
    assert len([c for c in ctl.calls if c[0] == "replan"]) == len(replans_before) + 1
