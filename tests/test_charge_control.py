"""Tests voor de generieke laadregelaar (met gesimuleerde lader en nepklok)."""

from __future__ import annotations

from custom_components.ev_planner.core.charge_control import (
    Action,
    ChargeRegulator,
    ChargerState,
    Desired,
    RegulatorConfig,
    decide_next_step,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.events = []

    def __call__(self):
        return self.now

    def schedule(self, delay, fn):
        self.events.append((self.now + delay, fn))

    async def sleep(self, seconds):
        self.now += seconds
        due = [e for e in self.events if e[0] <= self.now]
        self.events = [e for e in self.events if e[0] > self.now]
        for _, fn in sorted(due, key=lambda e: e[0]):
            fn()


class FakeCharger:
    """Simuleert vertragingen en bewaakt de veiligheidsinvarianten."""

    def __init__(self, clock, *, phases=3, current=0, mode="connected_requesting"):
        self.clock = clock
        self.available = True
        self.connected = True
        self.mode = mode
        self.current = current
        self.phases = phases
        self.commands = []
        self.violations = []
        self.refuse_start = False
        self.stop_works = True
        self.start_guard = None  # callable() -> bool (mag nu gestart worden?)

    def read(self):
        return ChargerState(
            available=self.available,
            connected=self.connected,
            charging=self.mode == "connected_charging",
            startable=self.mode in ("connected_requesting", "connected_finished"),
            current_a=self.current,
            phases=self.phases,
        )

    async def stop_charging(self):
        self.commands.append("stop")
        if self.stop_works:
            def done():
                if self.mode == "connected_charging":
                    self.mode = "connected_requesting"
            self.clock.schedule(3, done)

    async def start_charging(self):
        self.commands.append("start")
        if self.start_guard is not None and not self.start_guard():
            self.violations.append("gestart met verkeerde fase/stroom")
        if self.refuse_start:
            return

        def done():
            self.mode = "connected_charging"
        self.clock.schedule(2, done)

    async def set_current(self, amps):
        self.commands.append(f"current={amps}")

        def done():
            self.current = amps
        self.clock.schedule(1, done)

    async def set_phases(self, phases):
        self.commands.append(f"phases={phases}")
        if self.mode == "connected_charging":
            self.violations.append("fasewissel onder belasting")

        def done():
            self.phases = phases
        self.clock.schedule(10, done)


def _regulator(charger, clock, desired, *, notes=None, **cfg):
    notes = notes if notes is not None else []
    reg = ChargeRegulator(
        charger,
        lambda: desired["value"],
        RegulatorConfig(**cfg),
        clock=clock,
        sleep=clock.sleep,
        notify=lambda key, title, msg: notes.append(key),
    )
    reg.enabled = True
    reg.dry_run = False
    return reg


def _desired(allowed=True, phases=3, current=16):
    return {"value": Desired(allowed, phases, current)}


##############################################################################
# Beslislogica
##############################################################################


def _state(**kw):
    base = dict(
        available=True,
        connected=True,
        charging=False,
        startable=True,
        current_a=0,
        phases=3,
    )
    base.update(kw)
    return ChargerState(**base)


def _step(desired, state, **kw):
    args = dict(
        seconds_since_phase_switch=None,
        seconds_since_start=None,
        start_attempts=0,
    )
    args.update(kw)
    return decide_next_step(desired, state, RegulatorConfig(), **args)


def test_no_action_when_planner_charger_or_car_missing():
    assert _step(None, _state()).action is None
    assert _step(Desired(True, 3, 16), _state(available=False)).action is None
    assert _step(Desired(True, 3, 16), _state(connected=False)).action is None


def test_phase_switch_never_while_charging():
    step = _step(
        Desired(True, 1, 16),
        _state(charging=True, startable=False, phases=3, current_a=16),
    )
    assert step.action is Action.STOP


def test_unknown_phase_is_never_switched():
    step = _step(Desired(True, 1, 16), _state(phases=None))
    assert step.action is None


def test_not_allowed_stops_then_zeroes_current():
    step = _step(
        Desired(False, 3, 16),
        _state(charging=True, startable=False, current_a=16),
    )
    assert step.action is Action.STOP
    step = _step(Desired(False, 3, 16), _state(current_a=16))
    assert (step.action, step.value) == (Action.SET_CURRENT, 0)
    assert _step(Desired(False, 3, 16), _state(current_a=0)).action is None


def test_phase_switch_interval_is_respected():
    step = _step(Desired(True, 1, 16), _state(), seconds_since_phase_switch=10)
    assert step.action is None
    step = _step(Desired(True, 1, 16), _state(), seconds_since_phase_switch=120)
    assert (step.action, step.value) == (Action.SET_PHASE, 1)


##############################################################################
# Regelaar met gesimuleerde lader
##############################################################################


async def test_starts_charging_with_correct_current():
    clock = FakeClock()
    charger = FakeCharger(clock)
    reg = _regulator(charger, clock, _desired(True, 3, 16))

    await reg.reconcile()

    assert charger.commands == ["current=16", "start"]
    assert charger.mode == "connected_charging"


async def test_phase_change_stops_first_and_restarts_with_new_phase():
    clock = FakeClock()
    charger = FakeCharger(clock, phases=3, current=16, mode="connected_charging")
    desired = _desired(True, 1, 16)
    charger.start_guard = lambda: charger.phases == desired["value"].phases
    reg = _regulator(charger, clock, desired)

    await reg.reconcile()

    assert charger.commands == ["stop", "phases=1", "start"]
    assert charger.violations == []
    assert charger.phases == 1 and charger.mode == "connected_charging"


async def test_plan_change_during_phase_switch_never_starts_with_stale_phase():
    clock = FakeClock()
    charger = FakeCharger(clock, phases=3, current=16, mode="connected_charging")
    desired = _desired(True, 1, 16)
    charger.start_guard = lambda: (
        charger.phases == desired["value"].phases
        and charger.current == desired["value"].current_a
    )
    original_set_phases = charger.set_phases

    async def set_phases_and_change_plan(phases):
        await original_set_phases(phases)
        # Tijdens de (trage) fasewissel wil de planner opeens 3F/8A.
        desired["value"] = Desired(True, 3, 8)

    charger.set_phases = set_phases_and_change_plan
    reg = _regulator(charger, clock, desired, min_phase_switch_interval_s=0)

    await reg.reconcile()

    assert charger.violations == []
    assert charger.phases == 3 and charger.current == 8
    assert charger.mode == "connected_charging"


async def test_not_allowed_while_charging_stops_and_sets_zero():
    clock = FakeClock()
    charger = FakeCharger(clock, current=16, mode="connected_charging")
    reg = _regulator(charger, clock, _desired(False))

    await reg.reconcile()

    assert charger.commands == ["stop", "current=0"]
    assert charger.mode != "connected_charging"


async def test_disabled_regulator_does_nothing():
    clock = FakeClock()
    charger = FakeCharger(clock)
    reg = _regulator(charger, clock, _desired())
    reg.enabled = False

    await reg.reconcile()

    assert charger.commands == []
    assert reg.status.mode == "uit"


async def test_dry_run_logs_but_never_commands():
    clock = FakeClock()
    charger = FakeCharger(clock)
    reg = _regulator(charger, clock, _desired())
    reg.dry_run = True

    await reg.reconcile()
    await reg.reconcile()

    assert charger.commands == []
    assert reg.status.mode == "dry-run"
    assert reg.status.last_action.startswith("dry-run:")


async def test_unavailable_charger_is_left_alone():
    clock = FakeClock()
    charger = FakeCharger(clock)
    charger.available = False
    reg = _regulator(charger, clock, _desired())

    await reg.reconcile()

    assert charger.commands == []


async def test_charger_that_refuses_to_start_is_not_hammered():
    clock = FakeClock()
    charger = FakeCharger(clock, current=16)
    charger.refuse_start = True
    notes = []
    reg = _regulator(
        charger,
        clock,
        _desired(),
        notes=notes,
        start_cooldown_s=60,
        max_start_attempts=3,
    )

    for _ in range(12):
        await reg.reconcile()
        await clock.sleep(61)

    assert charger.commands.count("start") == 3
    assert reg.status.stalled
    assert notes.count("ev_planner_laadregeling_stalled") == 1


async def test_stall_is_reset_when_car_is_reconnected():
    clock = FakeClock()
    charger = FakeCharger(clock, current=16)
    charger.refuse_start = True
    reg = _regulator(
        charger,
        clock,
        _desired(),
        start_cooldown_s=0,
        max_start_attempts=2,
    )

    for _ in range(4):
        await reg.reconcile()
    assert charger.commands.count("start") == 2

    charger.connected = False
    await reg.reconcile()
    charger.connected = True
    charger.refuse_start = False
    await reg.reconcile()

    assert charger.mode == "connected_charging"


async def test_stop_timeout_gives_failure_backoff_and_notification():
    clock = FakeClock()
    charger = FakeCharger(clock, current=16, mode="connected_charging")
    charger.stop_works = False
    notes = []
    reg = _regulator(charger, clock, _desired(False), notes=notes)

    await reg.reconcile()
    assert "ev_planner_laadregeling_fout" in notes
    assert "time-out" in reg.status.last_error
    commands_after_first = list(charger.commands)

    await reg.reconcile()  # binnen backoff
    assert charger.commands == commands_after_first

    await clock.sleep(40)
    charger.stop_works = True
    await reg.reconcile()
    assert charger.mode != "connected_charging"
    assert reg.status.last_error is None


async def test_phase_control_disabled_ignores_desired_phase():
    clock = FakeClock()
    charger = FakeCharger(clock, phases=3, current=16)
    reg = _regulator(charger, clock, _desired(True, 1, 16), phase_control=False)

    await reg.reconcile()

    assert charger.commands == ["start"]
    assert charger.phases == 3
