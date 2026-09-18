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

from datetime import datetime, time, timedelta

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
    CONF_ENTITY_SOLAR_ENABLED,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    CONF_MAX_CHARGE_POWER_KW,
    DEFAULT_ENTITY_DEPARTURE,
    DEFAULT_ENTITY_DEPARTURE_DAY,
    DEFAULT_ENTITY_ENERGY_NEEDED,
    DEFAULT_ENTITY_MAX_PHASE_SWITCHES,
    DEFAULT_ENTITY_MAX_PRICE,
    DEFAULT_ENTITY_MIN_PV_KWH,
    DEFAULT_ENTITY_PLANNER_MODE,
    DEFAULT_ENTITY_PV_ROUNDING,
    DEFAULT_ENTITY_PRICES,
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


# Keys used by the native sensor platform.
DATA_SENSOR_STATE = "sensor_state"
DATA_SENSOR_DATA = "sensor_data"

# Key used by the native binary_sensor platform.
DATA_CHARGING_ALLOWED = "charging_allowed"


class EVPlannerController:
    """
    Centrale controller van de EV Planner.

    De controller koppelt Home Assistant aan de planner,
    scheduler en statusmanager.

    De controller heeft GEEN verantwoordelijkheid voor de charger.
    """

    def __init__(
        self,
        hass,
        logger: Logger,
        config=None,
        entry_id=None,
    ):
        self.logger = logger

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

        # Maximaal laadvermogen waarmee de planner rekent.
        #
        # Dit is GEEN opdracht aan de laadpaal.
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

        self.logger.debug("EV Planner controller geïnitialiseerd.")

        self.logger.debug(f"Smart Charging: {'aan' if self.enabled else 'uit'}")

    ##########################################################################
    # Native sensor data
    ##########################################################################

    def _get_entry_data(self) -> dict:
        """
        Geeft de gedeelde data van deze ConfigEntry terug.

        Deze data wordt gelezen door sensor.py.
        """

        domain_data = self._hass.data.setdefault(
            "ev_planner",
            {},
        )

        return domain_data.setdefault(
            self.entry_id,
            {},
        )

    def _signal_sensor_update(self) -> None:
        """
        Informeert de native sensors dat nieuwe plannerdata beschikbaar is.

        De planner kan vanuit async_add_executor_job() in een worker thread
        worden uitgevoerd. Daarom wordt de dispatcher-call thread-safe
        terug naar de Home Assistant event loop gepland.
        """

        self._hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self._hass,
            SIGNAL_SENSOR_UPDATE,
        )

    def _update_native_sensor_state(
        self,
        state: str,
    ) -> None:
        """
        Update de gedeelde state-data voor de native state sensor.
        """

        entry_data = self._get_entry_data()

        entry_data[DATA_SENSOR_STATE] = str(state)

        self._signal_sensor_update()

    def _update_native_sensor_data(
        self,
        state: str,
        attributes: dict,
    ) -> None:
        """
        Update de gedeelde data voor de native planner data sensor.
        """

        entry_data = self._get_entry_data()

        entry_data[DATA_SENSOR_DATA] = {
            "state": str(state),
            "attributes": dict(attributes),
        }

        self._signal_sensor_update()

    def _publish_charging_allowed(self) -> None:
        """
        Publiceert of de planner op dit moment laden toestaat.

        Dit is een afgeleide, alleen-lezen plannerstatus
        (scheduler.charging_allowed) en GEEN besturingsinput.
        """

        entry_data = self._get_entry_data()

        entry_data[DATA_CHARGING_ALLOWED] = bool(
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

    def _get_max_phase_switches(self) -> int:
        """
        Leest het maximaal toegestane aantal fasewisselingen.

        Dit is een plannerparameter.

        De planner voert de fasewisselingen zelf NIET uit.
        """

        raw_value = self.hass.get_state(self.entities["max_phase_switches"])

        try:
            value = int(float(raw_value))
        except (TypeError, ValueError):
            return 0

        return max(0, value)

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

        raw_energy = self.hass.get_state(self.entities["energy_needed"])

        try:
            energy_needed_kwh = float(raw_energy)
        except (TypeError, ValueError):
            self.logger.warning(f"Ongeldige benodigde energie: {raw_energy}")
            return None

        if energy_needed_kwh <= 0:
            self.logger.warning("Benodigde energie moet groter zijn dan 0 kWh.")
            return None

        # ------------------------------------------------------------------
        # Maximale prijs
        # ------------------------------------------------------------------

        raw_max_price = self.hass.get_state(self.entities["max_price"])

        try:
            max_price = float(raw_max_price)
        except (TypeError, ValueError):
            self.logger.warning(
                f"Ongeldige maximale elektriciteitsprijs: {raw_max_price}"
            )
            return None

        if max_price < 0:
            self.logger.warning("Maximale elektriciteitsprijs kan niet negatief zijn.")
            return None

        # ------------------------------------------------------------------
        # Minimale PV
        # ------------------------------------------------------------------

        raw_min_pv = self.hass.get_state(self.entities["min_pv_kwh"])

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

        raw_max_phase_switches = self.hass.get_state(
            self.entities["max_phase_switches"]
        )

        try:
            max_phase_switches = int(float(raw_max_phase_switches))
        except (TypeError, ValueError):
            self.logger.warning(
                f"Ongeldig maximaal aantal fasewisselingen: {raw_max_phase_switches}"
            )
            return None

        if max_phase_switches < 0:
            self.logger.warning(
                "Maximaal aantal fasewisselingen kan niet negatief zijn."
            )
            return None

        # ------------------------------------------------------------------
        # Vertrektijd
        # ------------------------------------------------------------------

        raw_departure = self.hass.get_state(self.entities["departure"])

        if not raw_departure:
            self.logger.warning("Geen vertrektijd ingesteld.")
            return None

        # ------------------------------------------------------------------
        # Vertrekdag
        # ------------------------------------------------------------------

        departure_day = self.hass.get_state(self.entities["departure_day"])

        if departure_day not in ("Vandaag", "Morgen"):
            self.logger.warning(f"Ongeldige vertrekdag: {departure_day}")
            return None

        # ------------------------------------------------------------------
        # Volledige datetime proberen
        # ------------------------------------------------------------------

        departure_datetime = None

        try:
            departure_datetime = datetime.fromisoformat(raw_departure)
        except (TypeError, ValueError):
            pass

        # ------------------------------------------------------------------
        # Alleen tijd
        # ------------------------------------------------------------------

        if departure_datetime is None:
            try:
                departure_clock = time.fromisoformat(raw_departure)

                if departure_day == "Vandaag":
                    departure_date = now.date()
                else:
                    departure_date = now.date() + timedelta(days=1)

                departure_datetime = datetime.combine(
                    departure_date,
                    departure_clock,
                    tzinfo=now.tzinfo,
                )

            except (TypeError, ValueError):
                self.logger.warning(f"Ongeldige vertrektijd: {raw_departure}")
                return None

        # ------------------------------------------------------------------
        # Timezone toevoegen indien nodig
        # ------------------------------------------------------------------

        if departure_datetime.tzinfo is None:
            departure_datetime = departure_datetime.replace(tzinfo=now.tzinfo)

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
                max_charge_power_kw=self.max_charge_power_kw,
                max_phase_switches=max_phase_switches,
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

        self.logger.debug(f"Max prijs: €{max_price:.3f}/kWh")

        self.logger.debug(f"Min PV: {min_pv_kwh:.2f} kWh")

        self.logger.debug(f"Max fasewisselingen: {max_phase_switches}")

        self.logger.debug(
            f"Vertrek: {departure_day} {departure_datetime:%d-%m-%Y %H:%M}"
        )

        return settings

    ##########################################################################
    # Planning
    ##########################################################################

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

        price_data = self.prices.read()

        if not price_data.hours:
            self.logger.warning("Geen prijsdata beschikbaar.")

            self._publish_planner_state("Geen prijsdata")

            return None

        # ------------------------------------------------------------------
        # Solcast
        # ------------------------------------------------------------------

        solcast_data = self.solcast.read()

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

        planner = EVPlanner(
            prices=price_data,
            solcast=solcast_data,
            settings=settings,
            logger=self.logger,
        )

        try:
            plan = planner.create_plan()

        except Exception as err:
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
        # Nieuwe planning opslaan
        # ------------------------------------------------------------------

        self.last_plan = plan
        self.last_plan_time = now

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
            "energy_planned_kwh": float(plan.energy_planned_kwh),
            "missing_energy_kwh": float(plan.missing_energy_kwh),
            "free_energy_kwh": float(plan.free_energy_kwh),
            "paid_energy_kwh": float(plan.paid_energy_kwh),
            "estimated_cost": float(plan.estimated_cost),
            "complete": bool(plan.complete),
            "departure_time": (plan.departure_time.isoformat()),
            "max_price": float(plan.max_price),
            "charging_windows": len(plan.decisions),
            "phase_switches": int(phase_switches),
            "max_phase_switches": int(max_phase_switches),
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
