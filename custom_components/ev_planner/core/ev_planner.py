"""
ev_planner.py

Hoofdcontroller van de EV Planner.

Verantwoordelijkheid:

    Home Assistant
        │
        ├── PriceReader
        ├── SolcastReader
        │
        ▼
    PlannerSettings
        │
        ▼
      EVPlanner
        │
        ▼
    ChargingPlan
        │
        ▼
    EVScheduler
        │
        ▼
    Planner output

Deze module bepaalt uitsluitend wat de EV Planner adviseert.

BELANGRIJK
----------

Deze controller stuurt GEEN laadpaal aan.

De controller:

- schakelt de charger niet aan of uit;
- stelt geen laadstroom in;
- stelt geen fase in;
- beheert geen charger mode;
- detecteert niet zelf of een auto aangesloten is;
- voert geen periodieke updates uit.

Home Assistant is verantwoordelijk voor:

- het periodiek aanroepen van update();
- bepalen wanneer opnieuw gepland moet worden;
- charger aan/uit;
- laadstroom;
- fasekeuze;
- reageren op het aansluiten van de auto.

De planner levert alleen de benodigde informatie aan Home Assistant.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import threading

from datetime import datetime, time, timedelta
from typing import NamedTuple

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from ..const import (
    CONF_ENTITY_DEPARTURE,
    CONF_ENTITY_DEPARTURE_DAY,
    CONF_ENTITY_ENERGY_NEEDED,
    CONF_ENTITY_MAX_PHASE_SWITCHES,
    CONF_ENTITY_MAX_PRICE,
    CONF_ENTITY_MIN_PV_KWH,
    CONF_ENTITY_PLANNER_MODE,
    CONF_ENTITY_PV_ROUNDING,
    CONF_ENTITY_PRICES,
    CONF_ENTITY_PRICE_FORECAST,
    CONF_ENTITY_SOLAR_ENABLED,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    CONF_MAX_CHARGE_POWER_KW,
    NATIVE_DEPARTURE,
    NATIVE_DEPARTURE_DAY,
    NATIVE_ENERGY_NEEDED,
    NATIVE_MAX_CHARGE_POWER,
    DEFAULT_PROGRESS_THRESHOLD_KWH,
    NATIVE_FORECAST_MARKUP,
    NATIVE_MAX_ENERGY_NEEDED,
    NATIVE_EXTRA_CHARGE_PRICE,
    DEFAULT_EXTRA_CHARGE_PRICE_CT,
    NATIVE_MAX_PAUSES,
    NATIVE_PROGRESS_THRESHOLD,
    MAX_PAUSES_UNLIMITED,
    NATIVE_MAX_PHASE_SWITCHES,
    NATIVE_MAX_PRICE,
    NATIVE_MIN_PV_KWH,
    DEFAULT_ENTITY_DEPARTURE,
    DEFAULT_ENTITY_DEPARTURE_DAY,
    DEFAULT_ENTITY_ENERGY_NEEDED,
    DEFAULT_ENTITY_MAX_PHASE_SWITCHES,
    DEFAULT_ENTITY_MAX_PRICE,
    DEFAULT_ENTITY_MIN_PV_KWH,
    DEFAULT_ENTITY_PLANNER_MODE,
    DEFAULT_ENTITY_PV_ROUNDING,
    DEFAULT_ENTITY_PRICES,
    DEFAULT_ENTITY_PRICE_FORECAST,
    FORECAST_PRICE_MARKUP,
    DEFAULT_ENTITY_SOLAR_ENABLED,
    DEFAULT_ENTITY_SOLCAST_TODAY,
    DEFAULT_ENTITY_SOLCAST_TOMORROW,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DOMAIN,
    PLANNER_MODE_NORMAL,
    PLANNER_MODE_SOLAR_ONLY,
    PV_ROUNDING_DOWN,
    PV_ROUNDING_UP,
    SIGNAL_SENSOR_UPDATE,
)
from .homeassistant import HomeAssistant
from .logger import Logger
from .planner import ChargingPlan, EVPlanner, PlannerSettings
from .prices import PriceReader
from .scheduler import EVScheduler, SchedulerSettings
from .solcast import SolcastReader
from .status import EVStatusManager


class DesiredCharge(NamedTuple):
    """Wat de planner op dit moment wil (voor de optionele laadregeling)."""

    allowed: bool
    phases: int
    current_a: int


def _locked(method):
    """Serialiseer planner-runs: services draaien in executor-threads."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._plan_lock:
            return method(self, *args, **kwargs)

    return wrapper


class EVPlannerController:
    """
    Centrale controller van de EV Planner.

    De controller koppelt Home Assistant aan de planner,
    scheduler en statusmanager.

    De controller heeft GEEN verantwoordelijkheid voor de charger.
    """

    # Klasse-defaults: ook geldig voor instanties zonder __init__ (tests).
    energy_override_kwh: float | None = None
    charge_manager = None

    # Tot wanneer de prijzen reikten toen het huidige plan werd gemaakt, en
    # het vertrek van dat plan. Zo is te zien of het plan op een onvolledige
    # prijshorizon (bv. morgen nog niet gepubliceerd) is gebaseerd.
    plan_prices_until: datetime | None = None
    plan_forecast_hours: int = 0
    plan_departure: datetime | None = None

    # Instellingen (tijd, dag) waarmee plan_departure is bepaald, en of een
    # automatische herplanning de oorspronkelijke vertrekdatum moet houden.
    _departure_inputs: tuple | None = None
    _keep_departure: bool = False

    # Wordt aangeroepen (vanuit de planner-thread) zodra de vertrekdatum van
    # het plan verandert; __init__.py gebruikt dit om hem op te slaan.
    departure_anchor_changed = None

    # Totaal-doel van de lopende laadsessie (gezet door de laadregeling), om
    # uit de resterende energie (override) het al geladen deel te halen.
    energy_delivered_kwh: float | None = None

    # (minimaal, maximaal) nog te laden energie van de laatste instellingen.
    _plan_bounds: tuple | None = None
    _extra_retry_after: datetime | None = None
    _last_horizon_check: datetime | None = None

    def __init__(
        self,
        hass,
        logger: Logger,
        config=None,
        entry_id=None,
    ):
        self.logger = logger

        # Reentrant: replan() roept create_plan() aan.
        self._plan_lock = threading.RLock()

        self.config = config or {}

        # ------------------------------------------------------------------
        # Home Assistant reference
        # ------------------------------------------------------------------

        self._hass = hass

        if entry_id is None:
            raise ValueError("EVPlannerController vereist een entry_id.")

        self.entry_id = entry_id

        # ------------------------------------------------------------------
        # Entity configuration
        #
        # Deze entities zijn uitsluitend INPUT voor de planner en komen
        # uit de ConfigEntry (data + options, samengevoegd door
        # __init__.py). Ontbrekende waarden vallen terug op de
        # standaard-entity-ID's uit const.py.
        #
        # De controller gebruikt geen charger entities.
        # ------------------------------------------------------------------

        self.entities = {
            "departure": self.config.get(
                CONF_ENTITY_DEPARTURE,
                DEFAULT_ENTITY_DEPARTURE,
            ),
            "departure_day": self.config.get(
                CONF_ENTITY_DEPARTURE_DAY,
                DEFAULT_ENTITY_DEPARTURE_DAY,
            ),
            "energy_needed": self.config.get(
                CONF_ENTITY_ENERGY_NEEDED,
                DEFAULT_ENTITY_ENERGY_NEEDED,
            ),
            "max_price": self.config.get(
                CONF_ENTITY_MAX_PRICE,
                DEFAULT_ENTITY_MAX_PRICE,
            ),
            "min_pv_kwh": self.config.get(
                CONF_ENTITY_MIN_PV_KWH,
                DEFAULT_ENTITY_MIN_PV_KWH,
            ),
            "max_phase_switches": self.config.get(
                CONF_ENTITY_MAX_PHASE_SWITCHES,
                DEFAULT_ENTITY_MAX_PHASE_SWITCHES,
            ),
            "pv_rounding": self.config.get(
                CONF_ENTITY_PV_ROUNDING,
                DEFAULT_ENTITY_PV_ROUNDING,
            ),
            "solar_enabled": self.config.get(
                CONF_ENTITY_SOLAR_ENABLED,
                DEFAULT_ENTITY_SOLAR_ENABLED,
            ),
        }

        # Het maximale laadvermogen wordt gelezen uit de native number entity.
        # De legacy configwaarde blijft alleen als fallback behouden.
        self.max_charge_power_kw = float(
            self.config.get(
                CONF_MAX_CHARGE_POWER_KW,
                DEFAULT_MAX_CHARGE_POWER_KW,
            )
        )

        # ------------------------------------------------------------------
        # Home Assistant wrapper
        # ------------------------------------------------------------------

        self.hass = HomeAssistant(
            hass=hass,
            logger=logger,
        )

        # ------------------------------------------------------------------
        # Readers
        # ------------------------------------------------------------------

        self.prices = PriceReader(
            app=self.hass,
            logger=logger,
            entity_id=self.config.get(
                CONF_ENTITY_PRICES,
                DEFAULT_ENTITY_PRICES,
            ),
            forecast_entity_id=self.config.get(
                CONF_ENTITY_PRICE_FORECAST,
                DEFAULT_ENTITY_PRICE_FORECAST,
            ),
            forecast_markup=FORECAST_PRICE_MARKUP,
        )

        self.solcast = SolcastReader(
            app=self.hass,
            logger=logger,
            entity_today=self.config.get(
                CONF_ENTITY_SOLCAST_TODAY,
                DEFAULT_ENTITY_SOLCAST_TODAY,
            ),
            entity_tomorrow=self.config.get(
                CONF_ENTITY_SOLCAST_TOMORROW,
                DEFAULT_ENTITY_SOLCAST_TOMORROW,
            ),
        )

        # ------------------------------------------------------------------
        # Scheduler
        #
        # De scheduler beheert de interne planning.
        # Hij stuurt GEEN charger aan.
        # ------------------------------------------------------------------

        self.scheduler = EVScheduler(
            settings=SchedulerSettings(),
            logger=logger,
        )

        # ------------------------------------------------------------------
        # Status
        # ------------------------------------------------------------------

        self.status = EVStatusManager(
            scheduler=self.scheduler,
            logger=logger,
        )

        # ------------------------------------------------------------------
        # Planner state
        # ------------------------------------------------------------------

        self.last_plan: ChargingPlan | None = None
        self.last_plan_time: datetime | None = None

        self.enabled = self._is_enabled()

        self.last_published_state = None

        # Runtime data for native entities. This belongs to the config entry,
        # not in the global hass.data dictionary.
        self.sensor_state = "Geen actieve laadbeslissing"
        self.sensor_data = {
            "state": "Geen planning",
            "attributes": {
                "energy_needed_kwh": 0.0,
                "energy_planned_kwh": 0.0,
                "missing_energy_kwh": 0.0,
                "free_energy_kwh": 0.0,
                "paid_energy_kwh": 0.0,
                "estimated_cost": 0.0,
                "complete": False,
                "departure_time": None,
                "max_price": 0.0,
                "charging_windows": 0,
                "phase_switches": 0,
                "max_phase_switches": 0,
                "total_charging_minutes": 0.0,
                "decisions": [],
            },
        }
        self.charging_allowed = False

        # Door de laadregeling gezette benodigde energie (kWh). None = de
        # ingestelde waarde gebruiken; 0 = doel bereikt, niets meer plannen.
        self.energy_override_kwh: float | None = None

        # Optionele laadregeling (charge_manager.ChargeManager).
        self.charge_manager = None

        self.logger.debug("EV Planner controller geïnitialiseerd.")

        self.logger.debug(f"Smart Charging: {'aan' if self.enabled else 'uit'}")

    ##########################################################################
    # Native sensor data
    ##########################################################################

    def _signal_sensor_update(self) -> None:
        """Notify native entities that planner data changed."""

        self._hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self._hass,
            SIGNAL_SENSOR_UPDATE,
        )

    def _update_native_sensor_state(self, state: str) -> None:
        """Update the planner state stored in ConfigEntry.runtime_data."""

        self.sensor_state = str(state)
        self._signal_sensor_update()

    def _update_native_sensor_data(
        self,
        state: str,
        attributes: dict,
    ) -> None:
        """Update planner data stored in ConfigEntry.runtime_data."""

        self.sensor_data = {
            "state": str(state),
            "attributes": dict(attributes),
        }
        self._signal_sensor_update()

    def _publish_charging_allowed(self) -> None:
        """Publish the read-only planner charging status."""

        self.charging_allowed = bool(
            self.status.get_status().charging_allowed
        )
        self._signal_sensor_update()

    ##########################################################################
    # Home Assistant input
    ##########################################################################

    def _smart_charging_entity_id(self) -> str | None:
        """
        Zoekt de actuele entity_id van de Smart Charging-switch op.

        De switch wordt door deze integratie zelf aangemaakt
        (unique_id = "{entry_id}_smart_charging"). Door op unique_id
        te zoeken in de entity registry blijft dit werken, ook als de
        entity handmatig hernoemd of verplaatst is. Een hardcoded
        entity_id (bijv. "switch.ev_planner_smart_charging") zou
        stilletjes breken zodra de gebruiker de entity hernoemt.
        """

        registry = er.async_get(self._hass)

        return registry.async_get_entity_id(
            "switch",
            DOMAIN,
            f"{self.entry_id}_smart_charging",
        )

    def _planner_mode_entity_id(self) -> str | None:
        """Return the native planner mode select entity_id."""

        registry = er.async_get(self._hass)

        entity_id = registry.async_get_entity_id(
            "select",
            DOMAIN,
            f"{self.entry_id}_planner_mode",
        )

        if entity_id is not None:
            return entity_id

        # Backward compatibility for existing configurations.
        return self.config.get(
            CONF_ENTITY_PLANNER_MODE,
            DEFAULT_ENTITY_PLANNER_MODE,
        )

    def _get_planner_mode(self) -> str:
        """Read the current planner mode from the native select."""

        entity_id = self._planner_mode_entity_id()

        if entity_id is None:
            return PLANNER_MODE_NORMAL

        value = self.hass.get_state(entity_id)

        if value in (PLANNER_MODE_NORMAL, PLANNER_MODE_SOLAR_ONLY):
            return value

        return PLANNER_MODE_NORMAL

    def _pv_rounding_entity_id(self) -> str | None:
        """Return the native PV rounding select entity_id."""

        registry = er.async_get(self._hass)

        entity_id = registry.async_get_entity_id(
            "select",
            DOMAIN,
            f"{self.entry_id}_pv_rounding",
        )

        if entity_id is not None:
            return entity_id

        return self.config.get(
            CONF_ENTITY_PV_ROUNDING,
            DEFAULT_ENTITY_PV_ROUNDING,
        )

    def _get_pv_rounding(self) -> str:
        """Read the current PV rounding mode."""

        entity_id = self._pv_rounding_entity_id()

        if entity_id is None:
            return PV_ROUNDING_DOWN

        value = self.hass.get_state(entity_id)

        if value in (PV_ROUNDING_DOWN, PV_ROUNDING_UP):
            return value

        return PV_ROUNDING_DOWN

    def _is_enabled(self) -> bool:
        """
        Controleert of de planner is ingeschakeld.

        Dit is uitsluitend een plannerinstelling.

        De toestand van deze switch heeft geen directe relatie
        met het aan- of uitschakelen van de charger.
        """

        entity_id = self._smart_charging_entity_id()

        if entity_id is None:
            return False

        return self.hass.get_state(entity_id) == "on"

    def _native_entity_id(
        self,
        platform: str,
        suffix: str,
        legacy_entity: str | None = None,
    ) -> str | None:
        """Return a native entity_id, with an optional legacy fallback."""

        registry = er.async_get(self._hass)
        entity_id = registry.async_get_entity_id(
            platform,
            DOMAIN,
            f"{self.entry_id}_{suffix}",
        )

        if entity_id is not None:
            return entity_id

        return legacy_entity

    def _native_number_state(
        self,
        suffix: str,
        legacy_entity: str,
        default: float,
    ) -> float:
        """Read a native number, falling back to the legacy input_number."""

        entity_id = self._native_entity_id(
            "number",
            suffix,
            legacy_entity,
        )
        raw_value = self.hass.get_state(entity_id) if entity_id else None

        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return float(default)

    def _get_max_phase_switches(self) -> int:
        """Return the configured maximum number of phase switches."""

        value = self._native_number_state(
            NATIVE_MAX_PHASE_SWITCHES,
            self.entities["max_phase_switches"],
            8.0,
        )
        return max(0, int(value))

    def _entity_energy_limits(self) -> tuple[float, float] | None:
        """(minimum, maximum) uit de number-entiteiten; max >= min."""

        raw_min = self._native_number_state(
            NATIVE_ENERGY_NEEDED,
            self.entities["energy_needed"],
            10.0,
        )
        raw_max = self._native_number_state(
            NATIVE_MAX_ENERGY_NEEDED,
            "",
            0.0,
        )

        try:
            minimum = max(0.0, float(raw_min))
            maximum = max(minimum, float(raw_max))
        except (TypeError, ValueError):
            self.logger.warning(
                f"Ongeldige benodigde energie: {raw_min} / {raw_max}"
            )
            return None

        if minimum != minimum or maximum != maximum:
            return None

        return minimum, maximum

    def _energy_bounds(self) -> tuple[float, float] | None:
        """Nog te laden energie: (minimaal, maximaal)."""

        limits = self._entity_energy_limits()

        if limits is None:
            return None

        minimum, maximum = limits

        delivered = self.energy_delivered_kwh

        if delivered is not None:
            delivered = max(0.0, float(delivered))

            return (
                max(0.0, minimum - delivered),
                max(0.0, maximum - delivered),
            )

        override = self.energy_override_kwh

        if override is None:
            return minimum, maximum

        try:
            remaining = float(override)
        except (TypeError, ValueError):
            self.logger.warning(f"Ongeldige benodigde energie: {override}")
            return None

        return remaining, remaining

    def get_entity_energy_target(self) -> float | None:
        """Doel waarop de laadregeling toetst: het minimum."""

        limits = self._entity_energy_limits()

        if limits is None:
            return None

        minimum, maximum = limits

        # Alleen een maximum ingesteld: alles is extra, doel = maximum.
        target = minimum if minimum > 0 else maximum

        return target if target > 0 else None

    def get_entity_energy_goal(self) -> float | None:
        """Einddoel van de sessie: het maximum (of het minimum)."""

        limits = self._entity_energy_limits()

        if limits is None:
            return None

        return limits[1] if limits[1] > 0 else None

    def get_extra_charge_price(self) -> float:
        """Prijslimiet (EUR/kWh) voor laden boven het minimum."""

        value = self._native_number_state(
            NATIVE_EXTRA_CHARGE_PRICE,
            "",
            DEFAULT_EXTRA_CHARGE_PRICE_CT,
        )

        if value != value:
            value = DEFAULT_EXTRA_CHARGE_PRICE_CT

        return min(50.0, max(0.0, float(value))) / 100

    def get_forecast_markup(self) -> float:
        """Opslag (EUR/kWh) op voorspelde prijzen."""

        value = self._native_number_state(
            NATIVE_FORECAST_MARKUP,
            "",
            FORECAST_PRICE_MARKUP * 100,
        )

        if value != value:
            return FORECAST_PRICE_MARKUP

        return min(20.0, max(0.0, float(value))) / 100

    def departure_anchor_data(self) -> dict:
        """Vertrekdatum van het huidige plan, om op te slaan."""

        inputs = self._departure_inputs

        if self.plan_departure is None or not inputs:
            return {}

        return {
            "departure": self.plan_departure.isoformat(),
            "clock": inputs[0].isoformat(),
            "day": inputs[1],
        }

    def restore_departure_anchor(self, data) -> None:
        """Herstel de vertrekdatum na een herstart (fouten worden genegeerd)."""

        try:
            departure = datetime.fromisoformat(data["departure"])
            clock = time.fromisoformat(data["clock"])
            day = str(data["day"])
        except (KeyError, TypeError, ValueError):
            return

        if departure.tzinfo is None:
            return

        self.plan_departure = departure
        self._departure_inputs = (clock, day)

    def get_progress_threshold_kwh(self) -> float:
        """Afwijking (kWh) tijdens het laden waarbij de regeling bijstuurt."""

        value = self._native_number_state(
            NATIVE_PROGRESS_THRESHOLD,
            "",
            DEFAULT_PROGRESS_THRESHOLD_KWH,
        )

        if value != value or value <= 0:
            return DEFAULT_PROGRESS_THRESHOLD_KWH

        return min(3.0, max(0.1, float(value)))

    def _get_max_pauses(self) -> int | None:
        """Maximum pauses between charging periods; None = no limit."""

        value = self._native_number_state(
            NATIVE_MAX_PAUSES,
            "",
            float(MAX_PAUSES_UNLIMITED),
        )
        pauses = max(0, int(value))

        if pauses >= MAX_PAUSES_UNLIMITED:
            return None

        return pauses

    def _get_max_charge_power_kw(self) -> float:
        """Return the maximum charging power used by the planner."""

        return max(
            1.0,
            min(
                DEFAULT_MAX_CHARGE_POWER_KW,
                self._native_number_state(
                    NATIVE_MAX_CHARGE_POWER,
                    self.config.get(
                        CONF_MAX_CHARGE_POWER_KW,
                        "",
                    ),
                    DEFAULT_MAX_CHARGE_POWER_KW,
                ),
            ),
        )

    ##########################################################################
    # PlannerSettings
    ##########################################################################

    def _get_settings(
        self,
        now: datetime,
    ) -> PlannerSettings | None:
        """
        Leest alle PlannerSettings uit Home Assistant.
        """

        # ------------------------------------------------------------------
        # Benodigde energie
        # ------------------------------------------------------------------

        bounds = self._energy_bounds()

        if bounds is None:
            return None

        min_remaining, max_remaining = bounds

        if max_remaining <= 0:
            self.logger.debug("Benodigde energie is 0 kWh: niets te plannen.")
            return None

        self._plan_bounds = bounds

        # Minimum al gehaald, maar nog ruimte tot het maximum: alleen
        # extra laden met gratis PV of zeer goedkope stroom.
        cheap_only = min_remaining <= 1e-9

        energy_needed_kwh = max_remaining if cheap_only else min_remaining

        # ------------------------------------------------------------------
        # Maximale prijs
        # ------------------------------------------------------------------

        raw_max_price = self._native_number_state(
            NATIVE_MAX_PRICE,
            self.entities["max_price"],
            0.0,
        )

        try:
            # Native entity stores the price in euro cents per kWh.
            max_price = float(raw_max_price) / 100.0
        except (TypeError, ValueError):
            self.logger.warning(
                f"Ongeldige maximale elektriciteitsprijs: {raw_max_price}"
            )
            return None

        if max_price < 0:
            self.logger.warning("Maximale elektriciteitsprijs kan niet negatief zijn.")
            return None

        if cheap_only:
            max_price = min(max_price, self.get_extra_charge_price())

        # ------------------------------------------------------------------
        # Minimale PV
        # ------------------------------------------------------------------

        raw_min_pv = self._native_number_state(
            NATIVE_MIN_PV_KWH,
            self.entities["min_pv_kwh"],
            0.0,
        )

        try:
            min_pv_kwh = float(raw_min_pv)
        except (TypeError, ValueError):
            self.logger.warning(f"Ongeldige minimale PV-energie: {raw_min_pv}")
            return None

        if min_pv_kwh < 0:
            self.logger.warning("Minimale PV-energie kan niet negatief zijn.")
            return None

        # ------------------------------------------------------------------
        # Planner mode
        # ------------------------------------------------------------------

        planner_mode = self._get_planner_mode()

        # ------------------------------------------------------------------
        # PV afronding
        # ------------------------------------------------------------------

        pv_rounding = self._get_pv_rounding()

        # ------------------------------------------------------------------
        # Fasewisselingen
        # ------------------------------------------------------------------

        max_phase_switches = self._get_max_phase_switches()

        # ------------------------------------------------------------------
        # Vertrektijd
        # ------------------------------------------------------------------

        departure_entity = self._native_entity_id(
            "time",
            NATIVE_DEPARTURE,
            self.entities["departure"],
        )
        raw_departure = (
            self.hass.get_state(departure_entity) if departure_entity else None
        )

        if not raw_departure:
            self.logger.warning("Geen vertrektijd ingesteld.")
            return None

        # ------------------------------------------------------------------
        # Vertrekdag
        # ------------------------------------------------------------------

        departure_day_entity = self._native_entity_id(
            "select",
            NATIVE_DEPARTURE_DAY,
            self.entities["departure_day"],
        )
        departure_day = (
            self.hass.get_state(departure_day_entity)
            if departure_day_entity
            else None
        )

        if departure_day not in ("Vandaag", "Morgen"):
            self.logger.warning(f"Ongeldige vertrekdag: {departure_day}")
            return None

        # ------------------------------------------------------------------
        # Vertrektijd combineren met de gekozen vertrekdag
        #
        # De native datetime is bewust een tijd-only instelling. De
        # afzonderlijke vertrekdag bepaalt of die tijd vandaag of morgen
        # geldt. Dit voorkomt dat een interne datum van de datetime entity
        # de keuze "Morgen" overschrijft.
        # ------------------------------------------------------------------

        departure_clock = None

        try:
            departure_clock = datetime.fromisoformat(raw_departure).time()
        except (TypeError, ValueError):
            try:
                departure_clock = time.fromisoformat(raw_departure)
            except (TypeError, ValueError):
                self.logger.warning(f"Ongeldige vertrektijd: {raw_departure}")
                return None

        inputs = (departure_clock, departure_day)

        if departure_day == "Vandaag":
            departure_date = now.date()
        else:
            departure_date = now.date() + timedelta(days=1)

        departure_datetime = datetime.combine(
            departure_date,
            departure_clock,
            tzinfo=now.tzinfo,
        )

        # Automatische herplanning: de vertrekdatum van de bestaande
        # planning blijft gelden zolang de gebruiker tijd/dag niet wijzigde
        # en het vertrek nog niet geweest is. Anders zou "Morgen" op de
        # dag van vertrek opnieuw naar de volgende dag verschuiven.
        if (
            self._keep_departure
            and self.plan_departure is not None
            and self._departure_inputs == inputs
            and self.plan_departure > now
        ):
            departure_datetime = self.plan_departure
            self.logger.debug(
                "Automatische herplanning: oorspronkelijk vertrek "
                f"{departure_datetime:%d-%m-%Y %H:%M} blijft gelden."
            )

        self._pending_departure_inputs = inputs

        # ------------------------------------------------------------------
        # PlannerSettings
        # ------------------------------------------------------------------

        try:
            settings = PlannerSettings(
                energy_needed_kwh=energy_needed_kwh,
                departure_time=departure_datetime,
                max_price=max_price,
                min_pv_kwh=min_pv_kwh,
                solar_is_free=True,
                max_charge_power_kw=self._get_max_charge_power_kw(),
                max_phase_switches=max_phase_switches,
                max_pauses=self._get_max_pauses(),
                planner_mode=planner_mode,
                pv_rounding=pv_rounding,
            )

        except (TypeError, ValueError) as err:
            self.logger.warning(f"Ongeldige PlannerSettings: {err}")
            return None

        # ------------------------------------------------------------------
        # Debug logging
        # ------------------------------------------------------------------

        self.logger.debug("Planner instellingen:")

        self.logger.debug(f"Benodigd: {energy_needed_kwh:.2f} kWh")

        self.logger.debug(f"Max prijs: €{max_price:.2f}/kWh")

        self.logger.debug(f"Min PV: {min_pv_kwh:.2f} kWh")

        self.logger.debug(f"Max fasewisselingen: {max_phase_switches}")

        self.logger.debug(
            f"Vertrek: {departure_day} {departure_datetime:%d-%m-%Y %H:%M}"
        )

        return settings

    ##########################################################################
    # Planning
    ##########################################################################

    @_locked
    def create_plan(
        self,
        now: datetime | None = None,
    ) -> ChargingPlan | None:
        """
        Maakt een nieuwe laadplanning.

        Deze methode verandert uitsluitend de interne plannerstatus.

        Er wordt GEEN charger aangestuurd.
        """

        if now is None:
            now = datetime.now().astimezone()

        self.enabled = self._is_enabled()

        # ------------------------------------------------------------------
        # Planner uitgeschakeld
        # ------------------------------------------------------------------

        if not self.enabled:
            self.logger.debug("Smart Charging staat uit.")

            self.scheduler.clear_plan()

            self.last_plan = None
            self.last_plan_time = None

            self._publish_plan_data()

            self._publish_planner_state("Planner uitgeschakeld")

            return None

        # ------------------------------------------------------------------
        # Doel bereikt (gezet door de laadregeling): niets meer plannen
        # ------------------------------------------------------------------

        if self.energy_override_kwh is not None and self.energy_override_kwh <= 0:
            self.logger.info("Doel bereikt: geen laadplanning meer nodig.")
            self.scheduler.clear_plan()
            self.last_plan = None
            self.last_plan_time = None
            self._publish_plan_data()
            self._publish_planner_state("Doel bereikt")
            return None

        # ------------------------------------------------------------------
        # Planner instellingen
        # ------------------------------------------------------------------

        settings = self._get_settings(now)

        if settings is None:
            self.logger.warning("PlannerSettings konden niet worden opgebouwd.")

            self.scheduler.clear_plan()

            self.last_plan = None
            self.last_plan_time = None

            self._publish_plan_data()

            self._publish_planner_state("Ongeldige plannerinstellingen")

            return None

        # ------------------------------------------------------------------
        # Electricity prices
        # ------------------------------------------------------------------

        try:
            # Definitieve prijzen eerst; voorspelde prijzen alleen voor de
            # uren daarna (of voor alles als de definitieve prijzen niet
            # te lezen zijn). final_until blijft het einde van de
            # definitieve prijzen, zodat het plan wordt ververst zodra
            # die prijzen echt binnenkomen.
            try:
                self.prices.forecast_markup = self.get_forecast_markup()
            except Exception:  # noqa: BLE001 - dan blijft de vorige opslag gelden
                pass

            price_data, final_until = self.prices.read_with_forecast()

        except Exception as err:
            self.logger.warning(f"Prijsdata kon niet worden gelezen: {err}")

            self._publish_planner_state("Geen prijsdata")

            return None

        if not price_data.hours:
            self.logger.warning("Geen prijsdata beschikbaar.")

            self._publish_planner_state("Geen prijsdata")

            return None

        # ------------------------------------------------------------------
        # Solcast
        # ------------------------------------------------------------------

        try:
            solcast_data = self.solcast.read()

        except Exception as err:
            self.logger.warning(f"Solcast-data kon niet worden gelezen: {err}")

            self._publish_planner_state("Geen PV-data")

            return None

        if solcast_data is None:
            self.logger.warning("Geen Solcast-data beschikbaar.")

            self._publish_planner_state("Geen PV-data")

            return None

        # ------------------------------------------------------------------
        # Planner
        #
        # BUGFIX: planner.create_plan() kan intern een ValueError
        # opwerpen vanuit _validate_final_plan() (een defensieve
        # eindcontrole die "dit zou nooit mogen gebeuren"-situaties
        # afvangt). Die exception werd voorheen niet opgevangen en
        # crashte de hele service-call (ev_planner.update/
        # create_plan/replan) met een onbehandelde traceback, in
        # plaats van hetzelfde nette "geen laadplan"-pad te volgen
        # als de andere faalscenario's hieronder.
        # ------------------------------------------------------------------

        min_remaining, max_remaining = self._plan_bounds or (
            settings.energy_needed_kwh,
            settings.energy_needed_kwh,
        )
        cheap_only = min_remaining <= 1e-9

        if cheap_only and (
            self._extra_retry_after is not None and now < self._extra_retry_after
        ):
            self._publish_planner_state("Geen extra laadmomenten")
            return None

        # Een tweede planner-run (voor extra energie) gebruikt eigen kopieën.
        extra_inputs = None
        if not cheap_only and max_remaining > min_remaining + 0.05:
            extra_inputs = (
                copy.deepcopy(price_data),
                copy.deepcopy(solcast_data),
            )

        # Extra laden is optioneel: geen waarschuwing als er niets gevonden wordt.
        run_logger = (
            Logger(debug=self.logger._debug) if cheap_only else self.logger
        )

        planner = EVPlanner(
            prices=price_data,
            solcast=solcast_data,
            settings=settings,
            logger=run_logger,
        )

        try:
            plan = planner.create_plan()

        except Exception as err:
            if cheap_only:
                # Extra laden is optioneel: een rustige melding en later opnieuw.
                self._extra_retry_after = now + self.EXTRA_RETRY_INTERVAL
                self.logger.debug(f"Geen extra laadplan mogelijk: {err}")
                self.scheduler.clear_plan()
                self.last_plan = None
                self.last_plan_time = None
                self._publish_plan_data()
                self._publish_planner_state("Geen extra laadmomenten")
                return None

            self.logger.error(
                f"Onverwachte fout tijdens het maken van het laadplan: {err}"
            )

            self.scheduler.clear_plan()

            self.last_plan = None
            self.last_plan_time = None

            self._publish_plan_data()

            self._publish_planner_state("Fout bij plannen")

            return None

        if plan is None:
            self.logger.warning("Planner kon geen laadplan maken.")

            self.scheduler.clear_plan()

            self.last_plan = None
            self.last_plan_time = None

            self._publish_plan_data()

            self._publish_planner_state("Geen laadplan")

            return None

        # ------------------------------------------------------------------
        # Extra energie tot het maximum (gratis PV / zeer goedkoop)
        # ------------------------------------------------------------------

        if cheap_only:
            if plan.energy_planned_kwh <= 0.001:
                self._extra_retry_after = now + self.EXTRA_RETRY_INTERVAL
                self.logger.info(
                    "Minimum bereikt en geen gratis of zeer goedkope "
                    "laadmomenten voor extra energie."
                )
                self.scheduler.clear_plan()
                self.last_plan = None
                self.last_plan_time = None
                self._publish_plan_data()
                self._publish_planner_state("Geen extra laadmomenten")
                return None

            self._extra_retry_after = None
            plan = dataclasses.replace(
                plan,
                energy_needed_kwh=0.0,
                missing_energy_kwh=0.0,
                complete=True,
                extra_energy_kwh=float(plan.energy_planned_kwh),
            )

        elif extra_inputs is not None:
            plan = self._plan_with_extra(
                plan, settings, extra_inputs, min_remaining, max_remaining
            )

        # ------------------------------------------------------------------
        # Nieuwe planning opslaan
        # ------------------------------------------------------------------

        self.last_plan = plan
        self.last_plan_time = now
        self.plan_prices_until = final_until
        self.plan_forecast_hours = price_data.forecast_hours
        self.plan_departure = settings.departure_time
        self._departure_inputs = getattr(
            self, "_pending_departure_inputs", None
        )

        if self.departure_anchor_changed is not None:
            try:
                self.departure_anchor_changed()
            except Exception:  # noqa: BLE001 - opslaan mag plannen niet breken
                pass

        self.scheduler.set_plan(plan)

        # ------------------------------------------------------------------
        # Plannerdata publiceren
        # ------------------------------------------------------------------

        self._publish_plan_data()

        # ------------------------------------------------------------------
        # Actuele plannerbeslissing
        #
        # Alleen publiceren als DATA.
        #
        # Geen charger-aansturing.
        # ------------------------------------------------------------------

        current_decision = self._get_current_decision(now)

        self._publish_planner_decision(current_decision)

        self.logger.info("Nieuwe EV laadplanning gemaakt.")

        return plan

    ##########################################################################
    # Update
    ##########################################################################

    @_locked
    def update(
        self,
        now: datetime | None = None,
    ) -> None:
        """
        Voert één plannerupdate uit.

        Home Assistant bepaalt wanneer deze methode wordt aangeroepen.

        Deze methode:

        - maakt indien nodig een planning;
        - werkt de scheduler bij;
        - bepaalt de actuele plannerbeslissing;
        - werkt de plannerstatus bij.

        Deze methode stuurt GEEN charger aan.
        """

        if now is None:
            now = datetime.now().astimezone()

        self.enabled = self._is_enabled()

        # ------------------------------------------------------------------
        # Planner uitgeschakeld
        # ------------------------------------------------------------------

        if not self.enabled:
            self.scheduler.clear_plan()

            self.last_plan = None
            self.last_plan_time = None

            self._publish_plan_data()

            self._publish_planner_state("Planner uitgeschakeld")

            self.status.update(now)
            self._publish_charging_allowed()

            return

        # ------------------------------------------------------------------
        # Nog geen planning
        # ------------------------------------------------------------------

        if self.last_plan is None:
            plan = self.create_plan(now)

            if plan is None:
                self.status.update(now)
                self._publish_charging_allowed()
                return

        # ------------------------------------------------------------------
        # Plan verversen als er nieuwe prijzen zijn bijgekomen
        # ------------------------------------------------------------------

        elif self._new_prices_available(now):
            self.logger.info(
                "Nieuwe prijzen beschikbaar tot na de vorige prijshorizon: "
                "planning wordt vernieuwd."
            )
            self.replan_keep_previous(now)

        # ------------------------------------------------------------------
        # Scheduler update
        # ------------------------------------------------------------------

        changed = self.scheduler.update(now)

        # ------------------------------------------------------------------
        # Actuele plannerbeslissing
        # ------------------------------------------------------------------

        current_decision = self._get_current_decision(now)

        self._publish_planner_decision(current_decision)

        # ------------------------------------------------------------------
        # Status
        # ------------------------------------------------------------------

        self.status.update(now)

        self._publish_charging_allowed()

        if changed:
            self.status.log_status()

    ##########################################################################
    # Planning wissen
    ##########################################################################

    @_locked
    def clear_plan(self) -> None:
        """
        Verwijdert de huidige planning volledig.

        Er wordt geen charger aangestuurd.
        """

        self.scheduler.clear_plan()

        self.last_plan = None
        self.last_plan_time = None

        self._publish_planner_state("Geen planning")

        self._publish_plan_data()

        self.logger.info("EV Planner: planning volledig gewist.")

    ##########################################################################
    # Opnieuw plannen
    ##########################################################################

    @_locked
    def replan(
        self,
        now: datetime | None = None,
    ) -> ChargingPlan | None:
        """
        Verwijdert de huidige planning en maakt direct een nieuwe.

        Home Assistant bepaalt wanneer deze methode wordt aangeroepen.
        """

        if now is None:
            now = datetime.now().astimezone()

        self.scheduler.clear_plan()

        self._extra_retry_after = None

        self.last_plan = None
        self.last_plan_time = None

        self._publish_planner_state("Planning wordt vernieuwd")

        self._publish_plan_data()

        self.logger.info(
            "EV Planner: oude planning verwijderd, nieuwe planning wordt gemaakt."
        )

        plan = self.create_plan(now)

        if plan is None:
            self.logger.warning("EV Planner: nieuwe planning kon niet worden gemaakt.")
        else:
            self.logger.info("EV Planner: nieuwe planning succesvol gemaakt.")

        return plan

    ##########################################################################
    # Fasewisselingen
    ##########################################################################

    ##########################################################################
    # Alleen-lezen koppeling voor de optionele laadregeling
    ##########################################################################

    def get_desired_charge(
        self,
        now: datetime | None = None,
    ) -> DesiredCharge:
        """
        Wat de planner op dit moment wil.

        Alleen-lezen en zonder lock: wordt vanuit de event loop aangeroepen
        en mag dus nooit wachten op een lopende planning.
        """
        if now is None:
            now = datetime.now().astimezone()

        decision = self._get_current_decision(now)
        allowed = bool(self.scheduler.charging_allowed(now))

        if decision is None or not allowed:
            return DesiredCharge(False, 0, 0)

        return DesiredCharge(
            True,
            int(decision.phases),
            int(decision.charge_current_a),
        )

    def get_plan_decisions(self) -> list[tuple[datetime, datetime, float]]:
        """(start, einde, energie in kWh) van alle geselecteerde beslissingen."""
        plan = self.last_plan

        if plan is None:
            return []

        return [
            (
                decision.hour.start,
                decision.hour.end,
                float(decision.energy_kwh),
            )
            for decision in plan.decisions
            if decision.selected
        ]

    def energy_needed_entity_id(self) -> str | None:
        """Entity-id van de number met de benodigde energie."""
        return self._native_entity_id(
            "number",
            NATIVE_ENERGY_NEEDED,
            self.entities["energy_needed"],
        )

    def energy_needed_entity_ids(self) -> list[str]:
        """Minimum- en maximum-number (beide worden na afloop op 0 gezet)."""
        result = []
        for suffix, legacy in (
            (NATIVE_ENERGY_NEEDED, self.entities["energy_needed"]),
            (NATIVE_MAX_ENERGY_NEEDED, None),
        ):
            entity_id = self._native_entity_id("number", suffix, legacy)
            if entity_id:
                result.append(entity_id)
        return result

    def get_entity_energy_needed(self) -> float | None:
        """De ingestelde benodigde energie, zonder correctie van de regeling."""
        raw = self._native_number_state(
            NATIVE_ENERGY_NEEDED,
            self.entities["energy_needed"],
            10.0,
        )

        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None

        return value if value > 0 else None

    @_locked
    def replan_keep_previous(
        self,
        now: datetime | None = None,
    ) -> ChargingPlan | None:
        """
        Herplan, maar behoud de vorige planning als dat mislukt.

        Gewone replan() wist eerst het plan. Voor de automatische
        bijsturing is dat te riskant: een tijdelijk probleem met prijs- of
        PV-data zou dan een lopende laadsessie zonder plan achterlaten.
        Een doel dat bereikt is (override 0) telt NIET als mislukking.
        """
        previous_plan = self.last_plan
        previous_time = self.last_plan_time

        self._keep_departure = True
        try:
            plan = self.replan(now)
        finally:
            self._keep_departure = False

        goal_reached = (
            self.energy_override_kwh is not None and self.energy_override_kwh <= 0
        )

        if plan is None and previous_plan is not None and not goal_reached:
            self.last_plan = previous_plan
            self.last_plan_time = previous_time
            self.scheduler.set_plan(previous_plan)
            self._publish_plan_data()
            self.logger.warning(
                "Herplannen mislukt: de vorige planning blijft actief."
            )

        return plan

    PRICE_HORIZON_CHECK_INTERVAL = timedelta(minutes=5)
    EXTRA_RETRY_INTERVAL = timedelta(minutes=5)

    def _plan_with_extra(
        self,
        plan: ChargingPlan,
        settings: PlannerSettings,
        inputs: tuple,
        minimum: float,
        maximum: float,
    ) -> ChargingPlan:
        """
        Probeer boven het minimum extra energie te plannen.

        Tweede run: doel = maximum, maar alleen met gratis PV of slots
        onder de prijslimiet voor extra laden. Haalt die run minstens het
        minimum, dan wordt hij het plan; anders blijft het minimumplan.
        """

        if plan.missing_energy_kwh > 0.001:
            return plan

        extra_settings = copy.copy(settings)
        extra_settings.energy_needed_kwh = float(maximum)
        extra_settings.max_price = min(
            float(settings.max_price), self.get_extra_charge_price()
        )

        prices, solcast = inputs

        try:
            extra_plan = EVPlanner(
                prices=prices,
                solcast=solcast,
                settings=extra_settings,
                logger=Logger(debug=self.logger._debug),
            ).create_plan()
        except Exception as err:  # noqa: BLE001 - extra is optioneel
            self.logger.debug(f"Extra energie niet gepland: {err}")
            return plan

        if extra_plan is None or extra_plan.energy_planned_kwh < minimum - 1e-6:
            return plan

        extra = max(0.0, float(extra_plan.energy_planned_kwh) - minimum)

        self.logger.info(
            f"Extra laden ingepland: {extra:.2f} kWh boven het minimum "
            f"van {minimum:.2f} kWh (maximum {maximum:.2f} kWh)."
        )

        return dataclasses.replace(
            extra_plan,
            energy_needed_kwh=float(minimum),
            missing_energy_kwh=0.0,
            complete=True,
            extra_energy_kwh=extra,
        )

    def prices_cover_departure(self) -> bool | None:
        """True als de prijzen van het huidige plan tot het vertrek reiken."""
        if self.plan_prices_until is None or self.plan_departure is None:
            return None

        return self.plan_prices_until >= self.plan_departure

    def _new_prices_available(self, now: datetime) -> bool:
        """
        Zijn er sinds het maken van het plan prijzen bijgekomen terwijl dat
        plan de prijzen tot het vertrek nog niet dekte?

        De Zonneplan-prijzen van morgen verschijnen pas in de loop van de
        dag. Een plan dat eerder is gemaakt, kent die uren niet en zou
        anders blijven staan tot iemand herplant.

        Maximaal eens per 5 minuten gecontroleerd; zo wordt een mislukte
        herplanning niet elke ronde opnieuw geprobeerd.
        """
        if self.prices_cover_departure() is not False:
            return False

        last = self._last_horizon_check
        if last is not None and now - last < self.PRICE_HORIZON_CHECK_INTERVAL:
            return False

        self._last_horizon_check = now

        try:
            current_until = self.prices.read().valid_until
        except Exception:
            return False

        return (
            current_until is not None
            and self.plan_prices_until is not None
            and current_until > self.plan_prices_until
        )

    def _count_plan_pauses(self, plan: ChargingPlan | None) -> int:
        """Aantal pauzes tussen laadmomenten in het plan."""

        if plan is None or not plan.decisions:
            return 0

        pauses = 0
        previous_end = None

        for decision in sorted(plan.decisions, key=lambda d: d.hour.start):
            if not decision.selected:
                continue

            if previous_end is not None and decision.hour.start > previous_end:
                pauses += 1

            previous_end = decision.hour.end

        return pauses

    def _count_plan_phase_switches(
        self,
        plan: ChargingPlan | None,
    ) -> int:
        """
        Telt fasewisselingen tussen opeenvolgende beslissingen.

        Dit is uitsluitend een plannerberekening.
        """

        if plan is None or not plan.decisions:
            return 0

        switches = 0

        previous_phases = int(plan.decisions[0].phases)

        for decision in plan.decisions[1:]:
            current_phases = int(decision.phases)

            if current_phases != previous_phases:
                switches += 1

            previous_phases = current_phases

        return switches

    ##########################################################################
    # Huidige beslissing
    ##########################################################################

    def _get_current_decision(
        self,
        now: datetime,
    ):
        """
        Geeft de huidige plannerbeslissing terug.

        Dit is een plannerresultaat.

        De beslissing wordt NIET naar de charger gestuurd.
        """

        if self.last_plan is None:
            return None

        for decision in self.last_plan.decisions:
            if decision.hour.start <= now < decision.hour.end:
                return decision

        return None

    ##########################################################################
    # Decision naar dictionary
    ##########################################################################

    def _decision_to_dict(
        self,
        decision,
    ) -> dict:
        """
        Zet één plannerbeslissing om naar gewone Python-datatypen.

        Deze dictionary is DATA voor Home Assistant.

        charge_current_a en phases zijn advieswaarden.
        De controller voert ze niet uit.
        """

        try:
            pv_estimate = float(decision.hour.pv_estimate)
        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            pv_estimate = 0.0

        try:
            usable_pv = float(decision.hour.usable_pv)
        except (
            AttributeError,
            TypeError,
            ValueError,
        ):
            usable_pv = 0.0

        return {
            "start": decision.hour.start.isoformat(),
            "end": decision.hour.end.isoformat(),
            "energy_kwh": float(decision.energy_kwh),
            "free_energy_kwh": float(decision.free_energy_kwh),
            "paid_energy_kwh": float(decision.paid_energy_kwh),
            "price": float(decision.price),
            "price_is_forecast": bool(
                getattr(decision.hour, "is_forecast", False)
            ),
            "cost": float(decision.cost),
            # Planneradvies.
            #
            # Deze waarden worden NIET naar de charger gestuurd.
            "charge_power_kw": float(decision.charge_power_kw),
            "charge_current_a": int(decision.charge_current_a),
            "phases": int(decision.phases),
            "selected": bool(decision.selected),
            "reason": str(decision.reason),
            "pv_estimate_kwh": pv_estimate,
            "usable_pv_kwh": usable_pv,
        }

    ##########################################################################
    # Plan naar dictionary
    ##########################################################################

    def _plan_to_dict(
        self,
        plan: ChargingPlan,
    ) -> dict:
        """
        Zet een ChargingPlan om naar gewone Python-datatypen.
        """

        phase_switches = self._count_plan_phase_switches(plan)

        max_phase_switches = self._get_max_phase_switches()

        total_charging_minutes = 0.0

        decisions = []

        for decision in plan.decisions:
            duration_seconds = (decision.hour.end - decision.hour.start).total_seconds()

            if duration_seconds > 0:
                total_charging_minutes += float(duration_seconds) / 60.0

            decisions.append(self._decision_to_dict(decision))

        return {
            "energy_needed_kwh": float(plan.energy_needed_kwh),
            "extra_energy_kwh": float(getattr(plan, "extra_energy_kwh", 0.0)),
            "energy_planned_kwh": float(plan.energy_planned_kwh),
            "missing_energy_kwh": float(plan.missing_energy_kwh),
            "free_energy_kwh": float(plan.free_energy_kwh),
            "paid_energy_kwh": float(plan.paid_energy_kwh),
            "estimated_cost": float(plan.estimated_cost),
            "complete": bool(plan.complete),
            "prices_valid_until": (
                self.plan_prices_until.isoformat()
                if self.plan_prices_until is not None
                else None
            ),
            "prices_cover_departure": self.prices_cover_departure(),
            "forecast_prices_used": self.plan_forecast_hours > 0,
            "forecast_price_slots": self.plan_forecast_hours,
            "departure_time": (plan.departure_time.isoformat()),
            "max_price": float(plan.max_price),
            "charging_windows": len(plan.decisions),
            "phase_switches": int(phase_switches),
            "max_phase_switches": int(max_phase_switches),
            "pauses": int(self._count_plan_pauses(plan)),
            "max_pauses": self._get_max_pauses(),
            "total_charging_minutes": float(total_charging_minutes),
            "decisions": decisions,
        }

    ##########################################################################
    # Plannerdata publiceren
    ##########################################################################

    def _publish_plan_data(self) -> None:
        """
        Publiceert plannerdata voor de native sensor.

        De controller schrijft niet rechtstreeks naar een Home Assistant
        sensor. De native sensor leest de data uit hass.data.
        """

        if self.last_plan is None:
            attributes = {
                "energy_needed_kwh": 0.0,
                "energy_planned_kwh": 0.0,
                "missing_energy_kwh": 0.0,
                "free_energy_kwh": 0.0,
                "paid_energy_kwh": 0.0,
                "estimated_cost": 0.0,
                "complete": False,
                "departure_time": None,
                "max_price": 0.0,
                "charging_windows": 0,
                "phase_switches": 0,
                "max_phase_switches": 0,
                "total_charging_minutes": 0.0,
                "decisions": [],
            }

            self._update_native_sensor_data(
                state="Geen planning",
                attributes=attributes,
            )

            return

        plan = self.last_plan

        attributes = self._plan_to_dict(plan)

        if plan.complete:
            state = "Planning compleet"
        else:
            state = "Planning onvolledig"

        self._update_native_sensor_data(
            state=state,
            attributes=attributes,
        )

        self.logger.debug(
            "Plannerdata gepubliceerd: "
            f"{plan.energy_planned_kwh:.2f} kWh gepland, "
            f"{plan.free_energy_kwh:.2f} kWh gratis, "
            f"{plan.paid_energy_kwh:.2f} kWh betaald, "
            f"€{plan.estimated_cost:.2f}, "
            f"{attributes['phase_switches']} fasewisselingen."
        )

    ##########################################################################
    # Planner state
    ##########################################################################

    def _publish_planner_state(
        self,
        state: str,
    ) -> None:
        """
        Publiceert alleen de plannerstatus.

        Geen charger-aansturing.
        """

        state = str(state)

        if self.last_published_state is not None and self.last_published_state == state:
            return

        self._update_native_sensor_state(state)

        self.last_published_state = state

    ##########################################################################
    # Planner decision publiceren
    ##########################################################################

    def _publish_planner_decision(
        self,
        decision,
    ) -> None:
        """
        Publiceert de actuele plannerbeslissing als DATA.

        BELANGRIJK:

        Deze methode stuurt geen switch, number of charger aan.

        Home Assistant kan deze gegevens gebruiken om zelf:

        - laden aan/uit;
        - stroom;
        - fase

        te regelen.
        """

        if decision is None:
            self._publish_planner_state("Geen actieve laadbeslissing")
            return

        selected = bool(decision.selected)

        current_a = int(decision.charge_current_a)

        phases = int(decision.phases)

        power_kw = float(decision.charge_power_kw)

        power_w = int(round(power_kw * 1000.0))

        reason = str(decision.reason)

        if selected:
            state = f"Laden gepland: {current_a} A / {phases} fase(n) / {power_w} W"
        else:
            state = f"Niet laden: {reason}"

        self._publish_planner_state(state)

        self.logger.debug(
            "Planner-beslissing: "
            f"laden={selected}, "
            f"stroom={current_a} A, "
            f"fasen={phases}, "
            f"vermogen={power_w} W, "
            f"reden={reason}"
        )

    ##########################################################################
    # Dashboard
    ##########################################################################

    def get_dashboard_data(
        self,
        now: datetime | None = None,
    ) -> dict:
        """
        Geeft de actuele plannerinformatie voor het dashboard.
        """

        if now is None:
            now = datetime.now().astimezone()

        self.enabled = self._is_enabled()

        data = {
            "enabled": bool(self.enabled),
            "has_plan": self.last_plan is not None,
            "last_plan_time": (
                self.last_plan_time.isoformat()
                if self.last_plan_time is not None
                else None
            ),
        }

        try:
            data["status"] = self.status.as_dict()
        except Exception as err:
            self.logger.warning(f"Dashboard: status kon niet worden gelezen: {err}")

            data["status"] = {}

        if self.last_plan is None:
            data["plan"] = None
            data["decisions"] = []
            data["current_decision"] = None
            data["phase_switches"] = 0
            data["max_phase_switches"] = 0

            return data

        plan = self.last_plan

        plan_data = self._plan_to_dict(plan)

        data["plan"] = dict(plan_data)

        data["decisions"] = list(plan_data["decisions"])

        current_decision = self._get_current_decision(now)

        if current_decision is None:
            data["current_decision"] = None
        else:
            data["current_decision"] = self._decision_to_dict(current_decision)

        data["phase_switches"] = int(plan_data["phase_switches"])

        data["max_phase_switches"] = int(plan_data["max_phase_switches"])

        return data

    ##########################################################################
    # Status
    ##########################################################################

    def get_status(self) -> dict:
        """
        Geeft de actuele plannerstatus terug.
        """

        return self.status.as_dict()
