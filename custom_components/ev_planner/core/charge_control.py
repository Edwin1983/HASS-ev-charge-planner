"""
charge_control.py

Generieke laadregeling: brengt een lader naar de toestand die de planner
wil (laden toegestaan, aantal fasen, laadstroom).

Deze module kent GEEN Home Assistant. De lader wordt benaderd via de
kleine `ChargerPort`-interface; de HA-koppeling (entiteiten) staat in
charge_manager.py. Daardoor is de volledige logica te testen met een
gesimuleerde lader en een nepklok.

PRINCIPE
--------
De regelaar doet nooit "alles in een keer". Telkens wordt de actuele
toestand opnieuw gelezen en wordt de KLEINSTE VEILIGE STAP gezet:

    1. laden niet toegestaan  -> stoppen, daarna stroom op 0
    2. verkeerde fase         -> eerst stoppen, dan fase wisselen
    3. verkeerde stroom       -> stroom instellen
    4. alles klopt, lader wacht op start -> starten

Na elke stap wordt de planner OPNIEUW gelezen. Verandert het plan terwijl
een (trage) fasewissel loopt, dan wordt direct met het nieuwe plan
verder gewerkt en wordt nooit gestart met een fase die niet meer klopt.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable, Protocol

MAX_STEPS_PER_RECONCILE = 10


##############################################################################
# Datatypes
##############################################################################


@dataclass(frozen=True)
class Desired:
    """Wat de planner nu wil."""

    allowed: bool
    phases: int
    current_a: int


@dataclass(frozen=True)
class ChargerState:
    """Wat de lader nu doet."""

    available: bool
    connected: bool
    charging: bool
    startable: bool
    current_a: int | None
    phases: int | None


class Action(str, Enum):
    STOP = "stop"
    SET_PHASE = "set_phase"
    SET_CURRENT = "set_current"
    START = "start"


@dataclass(frozen=True)
class Step:
    """Resultaat van de beslislogica: een actie, of None met een reden."""

    action: Action | None
    value: int | None
    reason: str

    def describe(self) -> str:
        if self.action is None:
            return self.reason
        if self.value is None:
            return f"{self.action.value} ({self.reason})"
        return f"{self.action.value}={self.value} ({self.reason})"


@dataclass(frozen=True)
class RegulatorConfig:
    min_current_a: int = 6
    max_current_a: int = 16
    # Kan de lader van fase wisselen? Zo niet, dan wordt de gewenste fase
    # genegeerd.
    phase_control: bool = True
    stop_timeout_s: float = 30.0
    stop_settle_s: float = 2.0
    phase_timeout_s: float = 20.0
    phase_settle_s: float = 5.0
    current_timeout_s: float = 15.0
    start_timeout_s: float = 15.0
    poll_interval_s: float = 0.5
    # Bescherming van het relais.
    min_phase_switch_interval_s: float = 30.0
    start_cooldown_s: float = 120.0
    max_start_attempts: int = 3
    # Na zo lang geen plannerbeslissing: melding.
    planner_timeout_s: float = 900.0


class ChargerPort(Protocol):
    """De lader, abstract. `read` is synchroon en goedkoop."""

    def read(self) -> ChargerState: ...

    async def stop_charging(self) -> None: ...

    async def start_charging(self) -> None: ...

    async def set_current(self, amps: int) -> None: ...

    async def set_phases(self, phases: int) -> None: ...


@dataclass
class RegulatorStatus:
    mode: str = "uit"  # uit | dry-run | actief
    detail: str = "Nog niet gestart"
    last_action: str | None = None
    last_action_at: str | None = None
    last_error: str | None = None
    start_attempts: int = 0
    stalled: bool = False


##############################################################################
# Beslislogica (puur)
##############################################################################


def decide_next_step(
    desired: Desired | None,
    state: ChargerState,
    cfg: RegulatorConfig,
    *,
    seconds_since_phase_switch: float | None,
    seconds_since_start: float | None,
    start_attempts: int,
) -> Step:
    """Bepaal de volgende veilige stap. Puur: geen I/O, geen tijd."""

    if desired is None:
        return Step(None, None, "Planner onbereikbaar")

    if not state.available:
        return Step(None, None, "Lader onbereikbaar")

    if not state.connected:
        return Step(None, None, "Geen auto aangesloten")

    # ------------------------------------------------------------------
    # Laden niet toegestaan: stoppen en stroom op 0.
    # ------------------------------------------------------------------
    if not desired.allowed:
        if state.charging:
            return Step(Action.STOP, None, "laden niet toegestaan")
        if state.current_a not in (0, None):
            return Step(Action.SET_CURRENT, 0, "laden niet toegestaan")
        return Step(None, None, "Laden niet toegestaan")

    # ------------------------------------------------------------------
    # Plannerwaarden controleren.
    # ------------------------------------------------------------------
    if not cfg.min_current_a <= desired.current_a <= cfg.max_current_a:
        return Step(None, None, "Ongeldige plannerstroom")

    if cfg.phase_control:
        if desired.phases not in (1, 3):
            return Step(None, None, "Ongeldige plannerfase")
        if state.phases is None:
            return Step(None, None, "Werkelijke fase onbekend")

        # --------------------------------------------------------------
        # Fase wisselen: nooit onder belasting.
        # --------------------------------------------------------------
        if state.phases != desired.phases:
            if state.charging:
                return Step(Action.STOP, None, "fasewissel nodig")
            if (
                seconds_since_phase_switch is not None
                and seconds_since_phase_switch < cfg.min_phase_switch_interval_s
            ):
                return Step(None, None, "Wacht op minimale tijd tussen fasewissels")
            return Step(Action.SET_PHASE, desired.phases, "planner wil andere fase")

    # ------------------------------------------------------------------
    # Stroom.
    # ------------------------------------------------------------------
    if state.current_a != desired.current_a:
        return Step(Action.SET_CURRENT, desired.current_a, "planner wil andere stroom")

    # ------------------------------------------------------------------
    # Starten.
    # ------------------------------------------------------------------
    if state.startable and not state.charging:
        if start_attempts >= cfg.max_start_attempts:
            return Step(
                None,
                None,
                f"Lader start niet ({start_attempts} pogingen); gestopt",
            )
        if (
            seconds_since_start is not None
            and seconds_since_start < cfg.start_cooldown_s
        ):
            return Step(None, None, "Wacht op laadstart")
        return Step(Action.START, None, "planner staat laden toe")

    if state.charging:
        return Step(None, None, "Laden")
    return Step(None, None, "Gereed")


##############################################################################
# Regelaar
##############################################################################


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChargeRegulator:
    """Voert de stappen uit en bewaakt bevestigingen en time-outs."""

    def __init__(
        self,
        port: ChargerPort,
        desired_provider: Callable[[], Desired | None],
        config: RegulatorConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now_fn: Callable[[], datetime] = _utc_now,
        notify: Callable[[str, str, str], None] | None = None,
        clear_notification: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._port = port
        self._desired_provider = desired_provider
        self.config = config or RegulatorConfig()
        self._clock = clock
        self._sleep = sleep
        self._now_fn = now_fn
        self._notify = notify
        self._clear_notification = clear_notification
        self._log = log or (lambda message: None)

        self.enabled = False
        self.dry_run = True
        self.status = RegulatorStatus()

        self._lock = asyncio.Lock()
        self._last_phase_switch_at: float | None = None
        self._last_start_at: float | None = None
        self._start_attempts = 0
        self._consecutive_failures = 0
        self._backoff_until = 0.0
        self._last_dry_key: tuple | None = None
        self._desired_missing_since: float | None = None
        self._planner_alarm_sent = False
        self._stall_alarm_sent = False
        self._was_connected = False

    # ------------------------------------------------------------------
    # Publiek
    # ------------------------------------------------------------------

    def reset_stall(self) -> None:
        """Nieuwe kans voor een lader die niet wilde starten."""
        self._start_attempts = 0
        self._stall_alarm_sent = False
        self.status.stalled = False
        self.status.start_attempts = 0
        self._dismiss("ev_planner_laadregeling_stalled")

    async def reconcile(self) -> None:
        """Breng de lader naar de gewenste toestand (kleinste stappen)."""
        async with self._lock:
            self._refresh_mode()

            if not self.enabled:
                self.status.detail = "Laadregeling staat uit"
                return

            for _ in range(MAX_STEPS_PER_RECONCILE):
                if self._clock() < self._backoff_until:
                    self.status.detail = "Wacht na fout"
                    return

                desired = self._desired_provider()
                self._track_planner(desired)
                state = self._port.read()
                self._track_state(state)

                step = decide_next_step(
                    desired,
                    state,
                    self.config,
                    seconds_since_phase_switch=self._since(self._last_phase_switch_at),
                    seconds_since_start=self._since(self._last_start_at),
                    start_attempts=self._start_attempts,
                )
                self.status.detail = step.reason
                self._track_stall(step, state)

                if step.action is None:
                    self._last_dry_key = None
                    return

                if self.dry_run:
                    self._record_dry_run(step)
                    return

                if not await self._execute(step):
                    return

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    def _refresh_mode(self) -> None:
        if not self.enabled:
            self.status.mode = "uit"
        elif self.dry_run:
            self.status.mode = "dry-run"
        else:
            self.status.mode = "actief"

    def _since(self, moment: float | None) -> float | None:
        if moment is None:
            return None
        return self._clock() - moment

    def _track_state(self, state: ChargerState) -> None:
        if state.charging:
            if self._start_attempts:
                self.reset_stall()
        if state.available and state.connected and not self._was_connected:
            self.reset_stall()
        if state.available:
            self._was_connected = state.connected

    def _track_planner(self, desired: Desired | None) -> None:
        if desired is not None:
            self._desired_missing_since = None
            if self._planner_alarm_sent:
                self._planner_alarm_sent = False
                self._dismiss("ev_planner_laadregeling_planner")
            return

        now = self._clock()
        if self._desired_missing_since is None:
            self._desired_missing_since = now
        elif (
            not self._planner_alarm_sent
            and now - self._desired_missing_since >= self.config.planner_timeout_s
        ):
            self._planner_alarm_sent = True
            self._send(
                "ev_planner_laadregeling_planner",
                "EV Planner: planner onbereikbaar",
                "De planner levert al langere tijd geen beslissing. De lader "
                "blijft in de laatste toestand staan.",
            )

    def _track_stall(self, step: Step, state: ChargerState) -> None:
        self.status.start_attempts = self._start_attempts
        stalled = (
            step.action is None
            and state.startable
            and not state.charging
            and self._start_attempts >= self.config.max_start_attempts
        )
        self.status.stalled = stalled
        if stalled and not self._stall_alarm_sent:
            self._stall_alarm_sent = True
            self._send(
                "ev_planner_laadregeling_stalled",
                "EV Planner: lader start niet",
                f"De lader startte niet na {self._start_attempts} pogingen. "
                "Controleer de auto en de lader. De regeling probeert het "
                "opnieuw na het opnieuw aansluiten van de auto.",
            )

    def _record_dry_run(self, step: Step) -> None:
        key = (step.action, step.value)
        if key == self._last_dry_key:
            return
        self._last_dry_key = key
        self._log(f"Laadregeling (dry-run): zou uitvoeren: {step.describe()}")
        self.status.last_action = f"dry-run: {step.describe()}"
        self.status.last_action_at = self._now_fn().isoformat()

    async def _wait_for(
        self,
        predicate: Callable[[ChargerState], bool],
        timeout: float,
    ) -> bool:
        deadline = self._clock() + timeout
        while True:
            if predicate(self._port.read()):
                return True
            if self._clock() >= deadline:
                return False
            await self._sleep(self.config.poll_interval_s)

    async def _execute(self, step: Step) -> bool:
        cfg = self.config
        self._log(f"Laadregeling: {step.describe()}")

        try:
            if step.action is Action.STOP:
                await self._port.stop_charging()
                ok = await self._wait_for(lambda s: not s.charging, cfg.stop_timeout_s)
                if ok:
                    await self._sleep(cfg.stop_settle_s)

            elif step.action is Action.SET_PHASE:
                # Vooraf vastleggen: ook een mislukte poging telt, zodat
                # het relais niet wordt bestookt.
                self._last_phase_switch_at = self._clock()
                await self._port.set_phases(step.value)
                ok = await self._wait_for(
                    lambda s: s.phases == step.value, cfg.phase_timeout_s
                )
                if ok:
                    await self._sleep(cfg.phase_settle_s)

            elif step.action is Action.SET_CURRENT:
                await self._port.set_current(step.value)
                ok = await self._wait_for(
                    lambda s: s.current_a == step.value, cfg.current_timeout_s
                )

            else:  # START
                self._start_attempts += 1
                self._last_start_at = self._clock()
                await self._port.start_charging()
                # Een lader die niet start is geen fout, wel een poging.
                await self._wait_for(lambda s: s.charging, cfg.start_timeout_s)
                ok = True

        except Exception as err:  # noqa: BLE001 - poort mag alles gooien
            return self._fail(f"{step.describe()} mislukt: {err}")

        if not ok:
            return self._fail(f"{step.describe()}: geen bevestiging binnen de time-out")

        self._succeed(step)
        return True

    def _succeed(self, step: Step) -> None:
        self._consecutive_failures = 0
        self.status.last_error = None
        self.status.last_action = step.describe()
        self.status.last_action_at = self._now_fn().isoformat()
        self._dismiss("ev_planner_laadregeling_fout")

    def _fail(self, message: str) -> bool:
        self._consecutive_failures += 1
        delay = min(300.0, 30.0 * (2 ** (self._consecutive_failures - 1)))
        self._backoff_until = self._clock() + delay
        self.status.last_error = message
        self.status.detail = f"Fout: {message}"
        self._log(f"Laadregeling FOUT: {message} (nieuwe poging over {delay:.0f} s)")
        self._send(
            "ev_planner_laadregeling_fout",
            "EV Planner: laadregeling fout",
            f"{message}. Nieuwe poging over {delay:.0f} seconden.",
        )
        return False

    def _send(self, key: str, title: str, message: str) -> None:
        if self._notify is not None:
            self._notify(key, title, message)

    def _dismiss(self, key: str) -> None:
        if self._clear_notification is not None:
            self._clear_notification(key)
