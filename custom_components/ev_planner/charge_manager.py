"""
charge_manager.py

Home Assistant-koppeling voor de optionele laadregeling.

- `ChargerEntities` / `HAChargerPort`: een lader die uit gewone HA-entiteiten
  is opgebouwd (sensor voor de modus, number voor stroom en fase, switch
  voor start, knop of switch voor stop). Daardoor werkt het met elke lader
  die via entiteiten te sturen is.
- `ChargeManager`: draait elke 30 s en bij een wijziging van de lader-modus.
  Per ronde:
    1. sessie en energieboekhouding bijwerken,
    2. de planner verversen (eigen klok, geen automation nodig),
    3. na een laadblok gepland en werkelijk vergelijken en zo nodig
       opnieuw plannen met de werkelijk resterende energie,
    4. de lader naar de gewenste toestand brengen.

Alle logica staat in core/charge_control.py en core/energy_feedback.py
(zonder Home Assistant). Deze module is bewust dun en importeert Home
Assistant alleen op het moment dat het echt nodig is.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .const import (
    CONF_CHARGER_CHARGING_STATES,
    CONF_CHARGER_CONNECTED_STATES,
    CONF_CHARGER_CURRENT_ENTITY,
    CONF_CHARGER_ENERGY_ENTITY,
    CONF_CHARGER_MODE_ENTITY,
    CONF_CHARGER_PHASE_ENTITY,
    CONF_CHARGER_PHASE_ONE_VALUE,
    CONF_CHARGER_PHASE_THREE_VALUE,
    CONF_CHARGER_STARTABLE_STATES,
    CONF_CHARGER_START_ENTITY,
    CONF_CHARGER_STOP_ENTITY,
)
from .core.charge_control import (
    ChargeRegulator,
    ChargerState,
    Desired,
    RegulatorConfig,
)
from .core.energy_feedback import EnergyFeedback, FeedbackConfig

TICK_SECONDS = 30
SAVE_DELAY_SECONDS = 10
UNAVAILABLE = ("unknown", "unavailable", None, "")

DEFAULT_CONNECTED_STATES = (
    "connected_requesting,connected_charging,connected_finished"
)
DEFAULT_CHARGING_STATES = "connected_charging"
DEFAULT_STARTABLE_STATES = "connected_requesting,connected_finished"
DEFAULT_PHASE_ONE_VALUE = 32.0
DEFAULT_PHASE_THREE_VALUE = 0.0

TOGGLE_DEFAULTS = {
    "enabled": False,  # laadregeling actief
    "dry_run": True,  # alleen loggen wat er zou gebeuren
    "feedback": True,  # terugkoppeling gepland <-> werkelijk
}


def _split(value: Any, default: str) -> frozenset[str]:
    text = default if value in (None, "") else str(value)
    return frozenset(part.strip() for part in text.split(",") if part.strip())


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


##############################################################################
# Lader uit entiteiten
##############################################################################


@dataclass(frozen=True)
class ChargerEntities:
    mode_entity: str
    current_entity: str
    connected_states: frozenset[str]
    charging_states: frozenset[str]
    startable_states: frozenset[str]
    start_entity: str | None = None
    stop_entity: str | None = None
    phase_entity: str | None = None
    phase_one_value: float = DEFAULT_PHASE_ONE_VALUE
    phase_three_value: float = DEFAULT_PHASE_THREE_VALUE
    energy_entity: str | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ChargerEntities | None":
        """Geef None als de verplichte entiteiten niet zijn ingesteld."""
        mode = config.get(CONF_CHARGER_MODE_ENTITY)
        current = config.get(CONF_CHARGER_CURRENT_ENTITY)
        if not mode or not current:
            return None

        one = _float(config.get(CONF_CHARGER_PHASE_ONE_VALUE))
        three = _float(config.get(CONF_CHARGER_PHASE_THREE_VALUE))

        return cls(
            mode_entity=str(mode),
            current_entity=str(current),
            connected_states=_split(
                config.get(CONF_CHARGER_CONNECTED_STATES), DEFAULT_CONNECTED_STATES
            ),
            charging_states=_split(
                config.get(CONF_CHARGER_CHARGING_STATES), DEFAULT_CHARGING_STATES
            ),
            startable_states=_split(
                config.get(CONF_CHARGER_STARTABLE_STATES), DEFAULT_STARTABLE_STATES
            ),
            start_entity=config.get(CONF_CHARGER_START_ENTITY) or None,
            stop_entity=config.get(CONF_CHARGER_STOP_ENTITY) or None,
            phase_entity=config.get(CONF_CHARGER_PHASE_ENTITY) or None,
            phase_one_value=DEFAULT_PHASE_ONE_VALUE if one is None else one,
            phase_three_value=DEFAULT_PHASE_THREE_VALUE if three is None else three,
            energy_entity=config.get(CONF_CHARGER_ENERGY_ENTITY) or None,
        )


class HAChargerPort:
    """Een lader die via Home Assistant-entiteiten wordt bestuurd."""

    def __init__(
        self,
        hass: Any,
        entities: ChargerEntities,
        *,
        sleep: Callable[[float], Any] = asyncio.sleep,
        restart_pause_s: float = 2.0,
    ) -> None:
        self._hass = hass
        self._e = entities
        self._sleep = sleep
        self._restart_pause_s = restart_pause_s

    # ------------------------------------------------------------------

    def _state(self, entity_id: str | None):
        if entity_id is None:
            return None
        return self._hass.states.get(entity_id)

    def read(self) -> ChargerState:
        mode_state = self._state(self._e.mode_entity)
        mode = None if mode_state is None else mode_state.state

        if mode in UNAVAILABLE:
            return ChargerState(False, False, False, False, None, None)

        current_state = self._state(self._e.current_entity)
        current_value = _float(None if current_state is None else current_state.state)
        current = None if current_value is None else int(round(current_value))

        phases = None
        if self._e.phase_entity:
            phase_state = self._state(self._e.phase_entity)
            value = _float(None if phase_state is None else phase_state.state)
            if value is not None:
                if abs(value - self._e.phase_one_value) < 0.5:
                    phases = 1
                elif abs(value - self._e.phase_three_value) < 0.5:
                    phases = 3

        return ChargerState(
            available=True,
            connected=mode in self._e.connected_states,
            charging=mode in self._e.charging_states,
            startable=mode in self._e.startable_states,
            current_a=current,
            phases=phases,
        )

    # ------------------------------------------------------------------

    async def _call(
        self, domain: str, service: str, entity_id: str, **data: Any
    ) -> None:
        await self._hass.services.async_call(
            domain,
            service,
            {"entity_id": entity_id, **data},
            blocking=True,
        )

    async def stop_charging(self) -> None:
        stop = self._e.stop_entity
        if stop:
            if stop.startswith("button."):
                await self._call("button", "press", stop)
            else:
                await self._call(stop.split(".", 1)[0], "turn_off", stop)
            return
        if self._e.start_entity:
            await self._call("switch", "turn_off", self._e.start_entity)
            return
        raise RuntimeError("Geen stop-entiteit of start-schakelaar ingesteld")

    async def start_charging(self) -> None:
        start = self._e.start_entity
        if not start:
            # Laders zonder startschakelaar starten zelf zodra er een
            # stroom is ingesteld.
            return
        state = self._state(start)
        if state is not None and state.state == "on":
            # Schakelaar staat al aan maar de lader laadt niet: opnieuw
            # aan- en uitzetten geeft de lader een nieuwe startprikkel.
            await self._call("switch", "turn_off", start)
            await self._sleep(self._restart_pause_s)
        await self._call("switch", "turn_on", start)

    async def set_current(self, amps: int) -> None:
        await self._call("number", "set_value", self._e.current_entity, value=amps)

    async def set_phases(self, phases: int) -> None:
        if not self._e.phase_entity:
            raise RuntimeError("Geen fase-entiteit ingesteld")
        value = self._e.phase_one_value if phases == 1 else self._e.phase_three_value
        await self._call("number", "set_value", self._e.phase_entity, value=value)


def energy_kwh_from_state(state: Any) -> float | None:
    """Lees een energie-sensor als kWh (ondersteunt Wh)."""
    if state is None or state.state in UNAVAILABLE:
        return None
    value = _float(state.state)
    if value is None:
        return None
    unit = str(getattr(state, "attributes", {}).get("unit_of_measurement", "kWh"))
    if unit.lower() == "wh":
        value /= 1000.0
    return value


##############################################################################
# Manager
##############################################################################


class ChargeManager:
    """Orkestreert planner, terugkoppeling en lader."""

    def __init__(
        self,
        hass: Any,
        controller: Any,
        entities: ChargerEntities,
        *,
        store: Any,
        logger: Any,
        regulator_config: RegulatorConfig | None = None,
        feedback_config: FeedbackConfig | None = None,
        now_fn: Callable[[], datetime] | None = None,
        port: Any = None,
        regulator_runtime: dict[str, Any] | None = None,
    ) -> None:
        self._hass = hass
        self._controller = controller
        self.entities = entities
        self._store = store
        self._log = logger
        self._now_fn = now_fn or (lambda: datetime.now().astimezone())

        self.toggles: dict[str, bool] = dict(TOGGLE_DEFAULTS)
        self.feedback = EnergyFeedback(config=feedback_config)

        cfg = regulator_config or RegulatorConfig(
            phase_control=entities.phase_entity is not None
        )
        self.port = port or HAChargerPort(hass, entities)
        self.regulator = ChargeRegulator(
            self.port,
            self._desired,
            cfg,
            now_fn=self._now_fn,
            notify=self._notify,
            clear_notification=self._clear_notification,
            log=logger.info,
            **(regulator_runtime or {}),
        )

        self._tick_lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._started = False
        self._was_connected = False
        # Doel waarop de regeling toetst (het minimum) en het einddoel van de
        # sessie (het maximum; gelijk aan het doel zonder extra laden).
        self._session_entity_target: float | None = None
        self._session_goal: float | None = None
        self._seen_plan_time: datetime | None = None
        self._last_override: float | None = None
        self._boundary_unsub: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Levenscyclus
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        saved = data.get("toggles", {})
        for key in TOGGLE_DEFAULTS:
            if key in saved:
                self.toggles[key] = bool(saved[key])

        self.feedback = EnergyFeedback.from_dict(
            data.get("feedback"), self.feedback.config
        )
        target = data.get("session_entity_target")
        self._session_entity_target = None if target is None else float(target)
        goal = data.get("session_goal")
        self._session_goal = None if goal is None else float(goal)
        self._was_connected = self.feedback.session_active

    async def async_start(self) -> None:
        from homeassistant.helpers.event import (
            async_call_later,
            async_track_state_change_event,
            async_track_time_interval,
        )

        async def interval(now):
            await self._tick(self._now_fn())

        async def first(now):
            await self._tick(self._now_fn())

        self._unsubs.append(
            async_track_time_interval(
                self._hass, interval, timedelta(seconds=TICK_SECONDS)
            )
        )
        self._unsubs.append(async_call_later(self._hass, 5, first))
        self._unsubs.append(
            async_track_state_change_event(
                self._hass, [self.entities.mode_entity], self._mode_changed
            )
        )
        if self.entities.energy_entity:
            self._unsubs.append(
                async_track_state_change_event(
                    self._hass, [self.entities.energy_entity], self._energy_changed
                )
            )
        self._started = True

    async def async_stop(self) -> None:
        self._started = False
        self._cancel_boundary()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self._data())

    # ------------------------------------------------------------------
    # Schakelaars
    # ------------------------------------------------------------------

    async def async_set_toggle(self, key: str, value: bool) -> None:
        if key not in TOGGLE_DEFAULTS:
            raise KeyError(key)
        self.toggles[key] = bool(value)
        self._schedule_save()
        self._log.info(f"Laadregeling: {key} = {'aan' if value else 'uit'}")
        if self._started:
            self._hass.async_create_task(self._tick(self._now_fn()))

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _cancel_boundary(self) -> None:
        if self._boundary_unsub is not None:
            self._boundary_unsub()
            self._boundary_unsub = None

    def _schedule_boundary(self, now: datetime) -> None:
        """Plan een extra ronde op het eerstvolgende wisselmoment van het plan.

        Zonder dit duurt het tot 30 s voordat een nieuw laadblok, een andere
        stroom of een pauze door de lader wordt opgepakt.
        """
        if not self._started:
            return

        self._cancel_boundary()

        try:
            moments = [
                moment
                for start, end, _energy in self._controller.get_plan_decisions()
                for moment in (start, end)
                if moment > now
            ]
        except Exception:  # noqa: BLE001 - alleen een extra, optionele ronde
            return

        if not moments:
            return

        from homeassistant.helpers.event import async_track_point_in_utc_time

        target = min(moments) + timedelta(seconds=1)

        async def fire(_when):
            self._boundary_unsub = None
            await self._tick(self._now_fn())

        self._boundary_unsub = async_track_point_in_utc_time(
            self._hass, fire, target.astimezone(timezone.utc)
        )

    def _mode_changed(self, event: Any) -> None:
        if self._started:
            self._hass.async_create_task(self._tick(self._now_fn()))

    def _energy_changed(self, event: Any) -> None:
        value = energy_kwh_from_state(event.data.get("new_state"))
        self.feedback.ledger.record(self._now_fn(), value)

    # ------------------------------------------------------------------
    # Hulpfuncties
    # ------------------------------------------------------------------

    def _desired(self) -> Desired | None:
        try:
            wanted = self._controller.get_desired_charge(self._now_fn())
        except Exception as err:  # noqa: BLE001
            self._log.warning(f"Plannerbeslissing niet leesbaar: {err}")
            return None
        return Desired(wanted.allowed, wanted.phases, wanted.current_a)

    def _data(self) -> dict[str, Any]:
        return {
            "toggles": dict(self.toggles),
            "feedback": self.feedback.to_dict(),
            "session_entity_target": self._session_entity_target,
            "session_goal": self._session_goal,
        }

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY_SECONDS)

    def _notify(self, key: str, title: str, message: str) -> None:
        from homeassistant.components import persistent_notification

        persistent_notification.async_create(
            self._hass, message, title=title, notification_id=key
        )

    def _clear_notification(self, key: str) -> None:
        from homeassistant.components import persistent_notification

        persistent_notification.async_dismiss(self._hass, key)

    def _signal(self) -> None:
        try:
            from homeassistant.helpers.dispatcher import async_dispatcher_send

            from .const import SIGNAL_SENSOR_UPDATE

            async_dispatcher_send(self._hass, SIGNAL_SENSOR_UPDATE)
        except Exception:  # noqa: BLE001 - alleen een verversing van entiteiten
            pass

    @property
    def feedback_active(self) -> bool:
        return bool(self.toggles["feedback"] and self.entities.energy_entity)

    def _override_value(self) -> float | None:
        if not (self.feedback_active and self.feedback.session_active):
            return None
        goal = self._session_goal
        if goal is None:
            goal = self.feedback.target_kwh
        if goal is None:
            return None
        remaining = max(0.0, goal - self.feedback.delivered_kwh())
        if remaining <= self.feedback.config.done_tolerance_kwh:
            return 0.0
        return remaining

    def _plan_blocks_for_feedback(self) -> list:
        """
        Plan-blokken waartegen de regeling toetst: alleen het minimum.

        Extra energie (tot het maximum) is optioneel en telt niet mee voor
        afwijkingen: de eerste (minimum - al geladen) kWh van het plan
        tellen mee, de rest niet.
        """
        decisions = list(self._controller.get_plan_decisions())
        target = self.feedback.target_kwh
        goal = self._session_goal
        if target is None or goal is None or goal <= target + 1e-9:
            return decisions

        budget = max(0.0, target - self.feedback.delivered_kwh())
        result = []
        for start, end, energy in sorted(decisions, key=lambda d: d[0]):
            if budget <= 1e-9:
                break
            used = min(float(energy), budget)
            result.append((start, end, used))
            budget -= used
        return result

    async def _run(self, func: Callable[..., Any], *args: Any) -> Any:
        return await self._hass.async_add_executor_job(func, *args)

    def _sync_plan(self) -> None:
        """Leg een nieuw plan vast als basis voor de vergelijking."""
        plan_time = self._controller.last_plan_time
        if plan_time == self._seen_plan_time:
            return
        self._seen_plan_time = plan_time
        if plan_time is not None and self.feedback.session_active:
            self.feedback.on_new_plan(plan_time, self._plan_blocks_for_feedback())

    async def _reset_energy_needed(self) -> None:
        """Zet de number met de benodigde energie terug op 0 kWh."""
        getter = getattr(self._controller, "energy_needed_entity_ids", None)
        if getter is None:
            getter = lambda: [self._controller.energy_needed_entity_id()]  # noqa: E731
        for entity_id in getter():
            if not entity_id:
                continue
            try:
                await self._hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": 0},
                    blocking=True,
                )
            except Exception as err:  # noqa: BLE001 - alleen een nette reset
                self._log.warning(f"Benodigde energie niet op 0 gezet: {err}")

    async def async_reset_session(self, reset_energy: bool = True) -> None:
        """Beëindig de laadsessie (na laden voltooid of 'plan wissen')."""
        self.feedback.end_session()
        self._session_entity_target = None
        self._session_goal = None
        self._last_override = None
        self._controller.energy_override_kwh = None
        self._controller.energy_delivered_kwh = None
        if reset_energy:
            await self._reset_energy_needed()
        self._schedule_save()
        self._signal()

    def _apply_progress_threshold(self) -> None:
        """Neem de instelbare drempel voor de voortgangscontrole over."""
        getter = getattr(self._controller, "get_progress_threshold_kwh", None)
        if getter is None:
            return
        try:
            threshold = float(getter())
        except Exception:  # noqa: BLE001 - dan blijft de huidige drempel gelden
            return
        if threshold > 0 and threshold != self.feedback.config.progress_threshold_kwh:
            self.feedback.config = replace(
                self.feedback.config, progress_threshold_kwh=threshold
            )

    def _record_energy_now(self, now: datetime) -> None:
        if not self.entities.energy_entity:
            return
        state = self._hass.states.get(self.entities.energy_entity)
        self.feedback.ledger.record(now, energy_kwh_from_state(state))

    # ------------------------------------------------------------------
    # Een ronde
    # ------------------------------------------------------------------

    async def _tick(self, now: datetime) -> None:
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            try:
                await self._tick_inner(now)
            except Exception as err:  # noqa: BLE001 - een ronde mag nooit de timer breken
                self._log.error(f"Laadregeling: onverwachte fout in ronde: {err}")
            self._schedule_boundary(now)
            self._schedule_save()
            self._signal()

    async def _tick_inner(self, now: datetime) -> None:
        ctl = self._controller
        state = self.port.read()

        self._record_energy_now(now)

        # --- Sessie ------------------------------------------------------
        replan_needed = False
        if state.available:
            if state.connected != self._was_connected:
                self.feedback.note_connection(state.connected, now)
                if not state.connected:
                    self.feedback.end_session()
                    self._session_entity_target = None
                self._was_connected = state.connected

            if not self.feedback_active:
                if self.feedback.session_active:
                    self.feedback.end_session()
                    self._session_entity_target = None
                    replan_needed = True
            elif state.connected:
                target = ctl.get_entity_energy_target()
                goal_getter = getattr(ctl, "get_entity_energy_goal", None)
                goal = goal_getter() if goal_getter else target
                if target is not None and (
                    not self.feedback.session_active
                    or target != self._session_entity_target
                ):
                    self.feedback.start_session(target, now)
                    self._session_entity_target = target
                    self._session_goal = goal if goal is not None else target
                    replan_needed = True
                elif (
                    target is not None
                    and goal is not None
                    and goal != self._session_goal
                ):
                    # alleen het maximum is aangepast: bijplannen, sessie blijft
                    self._session_goal = goal
                    replan_needed = True

        # --- Planner verversen -------------------------------------------
        override = self._override_value()
        ctl.energy_override_kwh = override
        ctl.energy_delivered_kwh = (
            self.feedback.delivered_kwh()
            if self.feedback_active and self.feedback.session_active
            else None
        )

        goal_just_reached = override == 0.0 and self._last_override != 0.0
        self._last_override = override

        if replan_needed or goal_just_reached:
            await self._run(ctl.replan_keep_previous, now)
        await self._run(ctl.update, now)
        self._sync_plan()

        # --- Laden voltooid: benodigde energie terug op 0 ------------------
        if goal_just_reached:
            self._log.info("Laden voltooid: benodigde energie terug op 0 kWh.")
            await self.async_reset_session()

        # --- Gepland <-> werkelijk na elk laadblok -------------------------
        if self.feedback_active and self.feedback.session_active:
            evaluations = self.feedback.evaluate(now)
            for evaluation in evaluations:
                self._log.info(
                    "Laadblok beoordeeld: gepland "
                    f"{evaluation.planned_kwh:.2f} kWh, werkelijk "
                    f"{evaluation.actual_kwh:.2f} kWh, verschil "
                    f"{evaluation.deviation_kwh:+.2f} kWh"
                    + (
                        f" (overgeslagen: {evaluation.skipped_reason})"
                        if evaluation.skipped_reason
                        else ""
                    )
                )

            # Tijdens een lopend blok: nog op schema? Zo niet, dan nu al
            # bijsturen zodat het blok kan doorlopen.
            self._apply_progress_threshold()
            progress = None
            if state.connected and not self.feedback.should_replan(evaluations, now):
                progress = self.feedback.check_progress(now)
                if not self.feedback.should_replan_progress(progress, now):
                    progress = None
                else:
                    self._log.info(
                        "Laadvoortgang wijkt af: gepland "
                        f"{progress.planned_kwh:.2f} kWh, werkelijk "
                        f"{progress.actual_kwh:.2f} kWh, verschil "
                        f"{progress.deviation_kwh:+.2f} kWh"
                    )

            if progress is not None or self.feedback.should_replan(evaluations, now):
                self.feedback.mark_replanned(now)
                override = self._override_value()
                ctl.energy_override_kwh = override
                ctl.energy_delivered_kwh = self.feedback.delivered_kwh()
                self._last_override = override
                self._log.info(
                    "Planning wordt bijgesteld met "
                    f"{0.0 if override is None else override:.2f} kWh resterend"
                )
                await self._run(ctl.replan_keep_previous, now)
                await self._run(ctl.update, now)
                self._sync_plan()

        # --- Lader --------------------------------------------------------
        self.regulator.enabled = bool(self.toggles["enabled"])
        self.regulator.dry_run = bool(self.toggles["dry_run"])

        if self.regulator.enabled and not ctl.enabled:
            # Smart Charging staat uit: de regeling geeft de lader vrij.
            self.regulator.enabled = False
            await self.regulator.reconcile()
            self.regulator.status.detail = "Smart Charging staat uit"
            return

        await self.regulator.reconcile()

    # ------------------------------------------------------------------
    # Voor de entiteiten
    # ------------------------------------------------------------------

    def status_attributes(self) -> dict[str, Any]:
        status = self.regulator.status
        return {
            "mode": status.mode,
            "last_action": status.last_action,
            "last_action_at": status.last_action_at,
            "last_error": status.last_error,
            "start_attempts": status.start_attempts,
            "stalled": status.stalled,
            "feedback_enabled": self.feedback_active,
        }

    def energy_attributes(self) -> dict[str, Any]:
        return self.feedback.summary()
