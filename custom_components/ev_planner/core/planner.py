"""
planner.py

Slimme EV-laadplanner.

Combineert:

- uur- en kwartierprijzen
- Solcast PV-voorspelling
- benodigde hoeveelheid energie
- maximale prijs
- minimale bruikbare PV
- vertrektijd
- maximaal aantal fasewisselingen

Doel:

De benodigde energie zo goedkoop mogelijk laden
voordat de auto vertrekt.

Zonne-energie wordt beschouwd als gratis.

Deze module werkt uitsluitend met Python-objecten.

Home Assistant entities en dictionaries horen hier niet thuis.

De daadwerkelijke bediening van de laadpaal gebeurt
door Home Assistant / de runtime-laag.


LAADLOGICA

- 6 A t/m 16 A
- laadstroom altijd een geheel getal
- laadstroom mag per uur verschillen
- fasegrens wordt bepaald door config.PHASE_CHANGE_POWER
- maximaal aantal fasewisselingen is instelbaar
- een geselecteerd uur is één volledig laadvenster
- binnen een gepland uur wordt niet bewust aan/uit geschakeld
- een uur wordt dus niet opgesplitst in meerdere laadblokken
- de planner bepaalt faseconfiguratie, laadstroom en werkelijk vermogen
- energie = werkelijk vermogen × werkelijke laadduur
- energie kan exact worden begrensd op de benodigde hoeveelheid
- tussenliggende laaduren zijn volledig
- het eerste laadvenster mag later beginnen
- het laatste laadvenster mag eerder stoppen
- als één laadvenster voldoende is, mag dat venster later beginnen
- als meerdere laadvensters nodig zijn, zijn alle tussenliggende
  vensters volledig
- het eerste venster is alleen gedeeltelijk als daarmee de volledige
  benodigde energie al kan worden geladen
- het laatste venster is gedeeltelijk als daar de resterende energie
  in past
- daardoor kan exact de benodigde hoeveelheid energie worden geladen
- de laadstroom wordt naar een heel ampère naar boven afgerond
- een minimum van de geconfigureerde minimale laadstroom wordt gerespecteerd
- het werkelijke vermogen mag nooit boven max_charge_power_kw komen
- het werkelijke vermogen mag nooit boven het geconfigureerde
  3-fase technische maximum komen
- een 1-fase laadvermogen mag nooit boven MAX_POWER_1PH komen
- PV mag nooit groter worden gerapporteerd dan de beschikbare PV


OPTIMALISATIE

_optimize_hours() bepaalt in één keer, gezamenlijk: welke uren
geladen worden, met welke fase, en hoeveel energie per uur --
zodat de totale kosten minimaal zijn binnen het
fasewisselbudget. Dit gebeurt via dynamic programming over
gediscretiseerde energieniveaus (zie de docstring van
_optimize_hours() voor de volledige toelichting en de
wiskundige achtergrond). Uren selecteren en PAS DAARNA de fase
bepalen (twee gescheiden stappen) kan tot een duurder plan
leiden dan nodig, omdat de fasebeperking dan een uur kan
terugzetten naar minder vermogen dan tijdens de selectie werd
aangenomen.


ARCHITECTUUR

    PlannerSettings
          |
          v
      EVPlanner
          |
          +-- ChargingDecision
          |       |
          |       +-- charge_power_kw
          |       +-- charge_current_a
          |       +-- phases
          |
          v
      ChargingPlan

Hour bevat, naast prijs/PV-gegevens, ook de door de planner
bepaalde laadinstellingen (charge_power_w, charge_current_a,
phases). scheduler.py en status.py lezen deze rechtstreeks van
het actieve Hour-object — daar wordt niets herberekend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from time import perf_counter
import resource

from .logger import Logger
from .solar import apply_solar_only

from .models import Hour
from .models import PriceData
from .models import SolcastData

from .config import (
    MIN_CURRENT,
    MAX_CURRENT_1PH,
    VOLTAGE,
    MAX_POWER_1PH,
    MAX_POWER_3PH,
    PHASE_CHANGE_POWER,
)

from ..const import (
    PLANNER_MODE_NORMAL,
    PLANNER_MODE_SOLAR_ONLY,
    PV_ROUNDING_DOWN,
    PV_ROUNDING_UP,
)


##############################################################################
# TECHNISCHE CONSTANTEN
##############################################################################
#
# BELANGRIJK:
#
# Deze waarden worden NIET opnieuw berekend of hardcoded.
#
# config.py is de centrale bron voor de technische plannerinstellingen.
#
# De aliases hieronder behouden de bestaande namen binnen planner.py.
# Daardoor verandert de architectuur van deze module niet en blijven
# bestaande interne aanroepen gewoon werken.
##############################################################################

MIN_CHARGE_CURRENT_A = int(MIN_CURRENT)

MAX_CHARGE_CURRENT_A = int(MAX_CURRENT_1PH)

VOLTAGE_V = float(VOLTAGE)

MAX_POWER_1PH_KW = float(MAX_POWER_1PH)

MAX_POWER_3PH_KW = float(MAX_POWER_3PH)

PHASE_CHANGE_POWER_KW = float(PHASE_CHANGE_POWER)

ABSOLUTE_MAX_POWER_KW = float(MAX_POWER_3PH_KW)

# Harde bovengrens op het aantal fasewisselingen dat de planner
# ooit overweegt, ongeacht wat de gebruiker instelt.
#
# Zonder deze grens kan _optimize_hours() bij een hoge
# max_phase_switches in combinatie met een lange planningshorizon
# te veel rekentijd/geheugen vergen.
MAX_PHASE_SWITCHES_HARD_CAP = 8

# Verwaarloosbare "tiebreak" die bij EXACT gelijke werkelijke kosten
# (bijvoorbeeld: meerdere stroomsterktes die allemaal volledig
# gratis zijn binnen een uur) een hogere stroom licht laat
# voorkeuren boven een lagere. Zonder dit kiest de optimalisatie
# willekeurig (in de praktijk: altijd de laagste, eerst geprobeerde
# stroom) tussen gelijk-kostende opties, waardoor een uur met bv.
# 3 kWh beschikbare zon toch met het minimum van 6A (1,38 kWh)
# werd ingepland in plaats van de werkelijke opbrengst te
# benutten. De waarde is bewust astronomisch klein (1e-9) t.o.v.
# realistische prijsverschillen (doorgaans >= 0,0001), zodat dit
# NOOIT een echte prijsafweging kan omdraaien -- het breekt alleen
# gevallen waarin de kosten al exact identiek zijn.
CURRENT_TIEBREAK_EPSILON = 0.000000001
MEMORY_DIAGNOSTIC_INTERVAL = 8

# Compact predecessor encoding for direct DP reconstruction.
PARENT_ACTION_SHIFT = 16
PARENT_STATE_SHIFT = 8
PARENT_STATE_MASK = 0xFF
PARENT_ACTION_MASK = 0xFF
PARENT_ACTION_TYPE_MASK = 0xC0
PARENT_ACTION_FULL = 0x40
PARENT_ACTION_FINISH = 0x80
PARENT_PHASE_3 = 0x20
PARENT_CURRENT_MASK = 0x1F


def _process_rss_mb() -> float:
    """Return the current Python process RSS in MB for diagnostics."""
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024.0
    except (OSError, ValueError):
        pass

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


##############################################################################
# PlannerSettings
##############################################################################


class PlannerSettings:
    """
    Instellingen voor één laadopdracht.
    """

    def __init__(
        self,
        energy_needed_kwh: float,
        departure_time: datetime,
        max_price: float,
        min_pv_kwh: float = 0.0,
        solar_is_free: bool = True,
        max_charge_power_kw: float = 11.04,
        max_phase_switches: int = 2,
        planner_mode: str = PLANNER_MODE_NORMAL,
        pv_rounding: str = PV_ROUNDING_DOWN,
    ) -> None:

        self.energy_needed_kwh = float(energy_needed_kwh)

        self.departure_time = departure_time

        self.max_price = float(max_price)

        self.min_pv_kwh = float(min_pv_kwh)

        self.solar_is_free = bool(solar_is_free)

        self.max_charge_power_kw = float(max_charge_power_kw)

        self.max_phase_switches = int(max_phase_switches)

        self.planner_mode = str(planner_mode)
        self.pv_rounding = str(pv_rounding)

        self._validate()

    ##########################################################################
    # Validatie
    ##########################################################################

    def _validate(self) -> None:

        if self.energy_needed_kwh <= 0:
            raise ValueError("energy_needed_kwh moet groter zijn dan 0.")

        if not isinstance(
            self.departure_time,
            datetime,
        ):
            raise TypeError("departure_time moet een datetime zijn.")

        if self.departure_time.tzinfo is None:
            raise ValueError("departure_time moet timezone-aware zijn.")

        if self.max_price < 0:
            raise ValueError("max_price mag niet negatief zijn.")

        if self.min_pv_kwh < 0:
            raise ValueError("min_pv_kwh mag niet negatief zijn.")

        if not isinstance(
            self.solar_is_free,
            bool,
        ):
            raise TypeError("solar_is_free moet een boolean zijn.")

        if self.max_charge_power_kw <= 0:
            raise ValueError("max_charge_power_kw moet groter zijn dan 0.")

        if self.max_charge_power_kw > ABSOLUTE_MAX_POWER_KW:
            raise ValueError(
                "max_charge_power_kw mag niet groter zijn dan "
                f"{ABSOLUTE_MAX_POWER_KW:.2f} kW."
            )

        if self.max_phase_switches < 0:
            raise ValueError("max_phase_switches mag niet negatief zijn.")

        if self.planner_mode not in (PLANNER_MODE_NORMAL, PLANNER_MODE_SOLAR_ONLY):
            raise ValueError("Ongeldige planner_mode.")

        if self.pv_rounding not in (PV_ROUNDING_DOWN, PV_ROUNDING_UP):
            raise ValueError("Ongeldige pv_rounding.")


##############################################################################
# ChargingDecision
##############################################################################


@dataclass
class ChargingDecision:
    hour: Hour

    energy_kwh: float

    free_energy_kwh: float

    paid_energy_kwh: float

    price: float

    cost: float

    selected: bool

    reason: str

    charge_power_kw: float = 0.0

    charge_current_a: int = 0

    phases: int = 1


##############################################################################
# ChargingPlan
##############################################################################


@dataclass
class ChargingPlan:
    decisions: list[ChargingDecision]

    energy_needed_kwh: float

    energy_planned_kwh: float

    missing_energy_kwh: float

    free_energy_kwh: float

    paid_energy_kwh: float

    estimated_cost: float

    complete: bool

    departure_time: datetime

    max_price: float


##############################################################################
# EVPlanner
##############################################################################


class EVPlanner:
    """
    Centrale laadplanner.

    De planner bepaalt:

    - welke uren geselecteerd worden
    - faseconfiguratie
    - laadstroom
    - werkelijk laadvermogen
    - werkelijke laadduur
    - maximaal aantal fasewisselingen

    De planner bedient zelf geen laadpaal.
    """

    ##########################################################################
    # Constructor
    ##########################################################################

    def __init__(
        self,
        prices: PriceData,
        solcast: SolcastData,
        settings: PlannerSettings,
        logger: Logger,
    ) -> None:

        self.prices = prices

        self.solcast = solcast

        self.settings = settings

        self.logger = logger

    ##########################################################################
    # Publieke planner
    ##########################################################################

    def create_plan(
        self,
    ) -> ChargingPlan:
        """Build and validate a charging plan, with stage timing diagnostics."""
        perf_start = perf_counter()

        self._validate_settings()
        perf_after_validate = perf_counter()

        ######################################################################
        # Prijzen en Solcast combineren
        ######################################################################

        hours = self._build_hour_list()
        perf_after_build = perf_counter()

        ######################################################################
        # Alleen werkelijk beschikbare tijd
        ######################################################################

        hours = self._filter_available_time(hours)
        perf_after_filter = perf_counter()

        ######################################################################
        # Beschikbare PV bepalen
        ######################################################################

        hours = self._calculate_available_energy(hours)
        perf_after_energy = perf_counter()

        ######################################################################
        # Ieder uur voorbereiden
        ######################################################################

        for hour in hours:
            self._prepare_hour(hour)
        perf_after_prepare = perf_counter()

        ######################################################################
        # Haalbaarheid controleren
        ######################################################################

        self._check_feasibility(hours)
        perf_after_feasibility = perf_counter()

        ######################################################################
        # Uren, fase, stroom en venster gezamenlijk optimaliseren
        #
        # Bepaalt in één keer welke uren geladen worden, met welke
        # fase en stroomsterkte, en het exacte laadvenster -- zodat
        # de totale kosten minimaal zijn binnen het
        # fasewisselbudget, met harde uurgrenzen en hele-uur-blokken
        # (behalve het allerlaatste uur). Zie de toelichting bij
        # _optimize_hours() voor de volledige regels en achtergrond.
        ######################################################################

        for hour in hours:
            hour.original_start = hour.start

            hour.original_end = hour.end

        if self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY:
            apply_solar_only(self, hours)
        else:
            self._optimize_hours(hours)
        perf_after_optimize = perf_counter()

        selected = []

        for hour in hours:
            if hour.selected:
                selected.append(hour)

        selected.sort(key=lambda hour: hour.start)

        ######################################################################
        # Eindresultaat maken
        ######################################################################

        plan = self._build_plan(selected)
        perf_after_build_plan = perf_counter()

        ######################################################################
        # Eindcontrole
        ######################################################################

        self._validate_final_plan(plan)
        perf_after_validate_plan = perf_counter()

        ######################################################################
        # Logging
        ######################################################################

        self._log_plan(plan)
        perf_end = perf_counter()

        self.logger.info(
            "EV Planner PERF: "
            f"total={(perf_end - perf_start) * 1000.0:.1f} ms "
            f"| validate={(perf_after_validate - perf_start) * 1000.0:.1f} ms "
            f"| build={(perf_after_build - perf_after_validate) * 1000.0:.1f} ms "
            f"| filter={(perf_after_filter - perf_after_build) * 1000.0:.1f} ms "
            f"| energy={(perf_after_energy - perf_after_filter) * 1000.0:.1f} ms "
            f"| prepare={(perf_after_prepare - perf_after_energy) * 1000.0:.1f} ms "
            f"| feasibility={(perf_after_feasibility - perf_after_prepare) * 1000.0:.1f} ms "
            f"| optimize={(perf_after_optimize - perf_after_feasibility) * 1000.0:.1f} ms "
            f"| build_plan={(perf_after_build_plan - perf_after_optimize) * 1000.0:.1f} ms "
            f"| validate_plan={(perf_after_validate_plan - perf_after_build_plan) * 1000.0:.1f} ms "
            f"| log={(perf_end - perf_after_validate_plan) * 1000.0:.1f} ms "
            f"| hours={len(hours)} "
            f"| selected={len(plan.decisions)} "
            f"| target={self.settings.energy_needed_kwh:.3f} kWh "
            f"| mode={self.settings.planner_mode}"
        )

        return plan

    ##########################################################################
    # Instellingen controleren
    ##########################################################################

    def _validate_settings(
        self,
    ) -> None:

        energy_needed = float(self.settings.energy_needed_kwh)

        max_price = float(self.settings.max_price)

        max_power = float(self.settings.max_charge_power_kw)

        max_phase_switches = int(self.settings.max_phase_switches)

        if energy_needed <= 0:
            raise ValueError("Benodigde energie moet groter dan 0 kWh zijn.")

        if max_price < 0:
            raise ValueError("Maximale prijs mag niet negatief zijn.")

        if max_power <= 0:
            raise ValueError("Maximaal laadvermogen moet groter dan 0 kW zijn.")

        if max_power > ABSOLUTE_MAX_POWER_KW:
            raise ValueError(
                "Maximaal laadvermogen mag niet groter zijn dan "
                f"{ABSOLUTE_MAX_POWER_KW:.2f} kW."
            )

        if max_phase_switches < 0:
            raise ValueError("Maximaal aantal fasewisselingen mag niet negatief zijn.")

        if not isinstance(
            self.settings.departure_time,
            datetime,
        ):
            raise TypeError("Vertrektijd moet een datetime zijn.")

        if self.settings.departure_time.tzinfo is None:
            raise ValueError("Vertrektijd moet timezone-aware zijn.")

    ##########################################################################
    # Uurduur
    ##########################################################################

    def _hour_duration(
        self,
        hour: Hour,
    ) -> float:

        seconds = (hour.end - hour.start).total_seconds()

        return float(seconds / 3600.0)

    ##########################################################################
    # Solcast uur zoeken
    ##########################################################################

    def _find_solcast_hour(
        self,
        moment: datetime,
    ):

        solar_hours = self.solcast.hours

        for solar_hour in solar_hours:
            if solar_hour.start <= moment and moment < solar_hour.end:
                return solar_hour

        return None

    ##########################################################################
    # Uren combineren
    ##########################################################################

    def _build_hour_list(
        self,
    ) -> list[Hour]:

        hours = []

        price_hours = self.prices.hours

        for price_hour in price_hours:
            solar_hour = self._find_solcast_hour(price_hour.start)

            if solar_hour is None:
                self.logger.debug(
                    f"PV KOPPELING: {price_hour.start:%d-%m %H:%M} | GEEN SOLCAST UUR"
                )

            else:
                self.logger.debug(
                    f"PV KOPPELING: "
                    f"{price_hour.start:%d-%m %H:%M} "
                    f"| Solcast start="
                    f"{solar_hour.start:%d-%m %H:%M}"
                    f" | pv_estimate="
                    f"{float(solar_hour.pv_estimate):.3f} kWh"
                )

            hour = self._combine_hour_data(
                price_hour,
                solar_hour,
            )

            hours.append(hour)

        return hours

    ##########################################################################
    # Prijs en Solcast combineren
    ##########################################################################

    def _combine_hour_data(
        self,
        price_hour: Hour,
        solar_hour,
    ) -> Hour:

        hour = Hour(
            start=price_hour.start,
            end=price_hour.end,
            price=float(price_hour.price),
            price_raw=float(price_hour.price_raw),
            tariff_group=str(price_hour.tariff_group),
            sustainability_score=float(price_hour.sustainability_score),
            hour_index=int(price_hour.hour_index),
        )

        if solar_hour is not None:
            ##################################################################
            # Een Solcast-record kan een uur beslaan terwijl PriceReader
            # inmiddels kwartierslots levert. Verdeel de PV-prognose
            # daarom evenredig over de overlap, zodat dezelfde
            # zonnestroom niet vier keer wordt meegenomen.
            ##################################################################

            price_start = price_hour.start
            price_end = price_hour.end

            solar_start = solar_hour.start
            solar_end = solar_hour.end

            overlap_start = max(
                price_start,
                solar_start,
            )

            overlap_end = min(
                price_end,
                solar_end,
            )

            overlap_seconds = (
                overlap_end - overlap_start
            ).total_seconds()

            solar_seconds = (
                solar_end - solar_start
            ).total_seconds()

            fraction = 0.0

            if (
                overlap_seconds > 0
                and solar_seconds > 0
            ):
                fraction = (
                    overlap_seconds
                    / solar_seconds
                )

            hour.pv_estimate = (
                float(solar_hour.pv_estimate)
                * fraction
            )

            hour.pv_estimate10 = (
                float(solar_hour.pv_estimate10)
                * fraction
            )

            hour.pv_estimate90 = (
                float(solar_hour.pv_estimate90)
                * fraction
            )

        else:
            hour.pv_estimate = 0.0

            hour.pv_estimate10 = 0.0

            hour.pv_estimate90 = 0.0

        return hour

    ##########################################################################
    # Beschikbare uren vanaf NU tot vertrek
    ##########################################################################

    def _filter_available_time(
        self,
        hours: list[Hour],
    ) -> list[Hour]:
        """
        Beperkt de planning tot de werkelijk beschikbare tijd.

        Regels:

        - Uren die volledig voorbij zijn worden verwijderd.
        - Het huidige uur begint op het huidige tijdstip.
        - Toekomstige uren blijven volledig beschikbaar.
        - Het laatste uur mag worden afgekapt op de vertrektijd.

        Hierdoor kan de planner bijvoorbeeld om 16:56 niet meer
        doen alsof 16:00-17:00 nog een volledig laadvenster is.
        """

        departure = self.settings.departure_time

        ######################################################################
        # Huidige tijd bepalen
        #
        # Gebruik dezelfde timezone als de planning.
        ######################################################################

        now = datetime.now(departure.tzinfo)

        filtered_hours = []

        for source_hour in hours:
            ##################################################################
            # Uur ligt volledig vóór NU
            ##################################################################

            if source_hour.end <= now:
                continue

            ##################################################################
            # Uur begint ná vertrek
            ##################################################################

            if source_hour.start >= departure:
                continue

            ##################################################################
            # Begin van het beschikbare venster
            ##################################################################

            new_start = source_hour.start

            if source_hour.start < now:
                new_start = now

            ##################################################################
            # Einde van het beschikbare venster
            ##################################################################

            new_end = source_hour.end

            if source_hour.end > departure:
                new_end = departure

            ##################################################################
            # Ongeldig venster
            ##################################################################

            if new_start >= new_end:
                continue

            ##################################################################
            # Volledig origineel uur?
            ##################################################################

            if new_start == source_hour.start and new_end == source_hour.end:
                filtered_hours.append(source_hour)

                continue

            ##################################################################
            # Gedeeltelijk uur maken
            ##################################################################

            partial_hour = Hour(
                start=new_start,
                end=new_end,
                price=float(source_hour.price),
                price_raw=float(source_hour.price_raw),
                tariff_group=str(source_hour.tariff_group),
                sustainability_score=float(source_hour.sustainability_score),
                hour_index=int(source_hour.hour_index),
            )

            ##################################################################
            # Solcastgegevens proportioneel afschalen
            #
            # PV-waarden zijn energie voor het volledige broninterval.
            # Als het interval door NU of vertrek wordt afgekapt,
            # mag niet de volledige oorspronkelijke PV-energie aan het
            # kortere laadvenster worden toegekend.
            ##################################################################

            source_duration_seconds = (
                source_hour.end - source_hour.start
            ).total_seconds()

            partial_duration_seconds = (
                new_end - new_start
            ).total_seconds()

            fraction = 0.0

            if source_duration_seconds > 0:
                fraction = (
                    partial_duration_seconds
                    / source_duration_seconds
                )

            if fraction < 0.0:
                fraction = 0.0
            elif fraction > 1.0:
                fraction = 1.0

            partial_hour.pv_estimate = (
                float(source_hour.pv_estimate) * fraction
            )

            partial_hour.pv_estimate10 = (
                float(source_hour.pv_estimate10) * fraction
            )

            partial_hour.pv_estimate90 = (
                float(source_hour.pv_estimate90) * fraction
            )

            filtered_hours.append(partial_hour)

            ##################################################################
            # Logging
            ##################################################################

            self.logger.debug(
                f"TIJDFILTER: "
                f"{source_hour.start:%d-%m %H:%M}"
                f"-{source_hour.end:%H:%M}"
                f" -> "
                f"{new_start:%d-%m %H:%M}"
                f"-{new_end:%H:%M}"
            )

        return filtered_hours

    ##########################################################################
    # Beschikbare energie
    ##########################################################################

    def _calculate_available_energy(
        self,
        hours: list[Hour],
    ) -> list[Hour]:

        max_power = float(self.settings.max_charge_power_kw)

        ######################################################################
        # Nooit boven het absolute technische maximum.
        ######################################################################

        if max_power > ABSOLUTE_MAX_POWER_KW:
            max_power = ABSOLUTE_MAX_POWER_KW

        for hour in hours:
            duration = self._hour_duration(hour)

            if duration <= 0:
                hour.usable_pv = 0.0

                continue

            max_charge_energy = max_power * duration

            original_pv = float(hour.pv_estimate)

            if original_pv < 0:
                original_pv = 0.0

            pv = original_pv

            ##################################################################
            # Minimale bruikbare PV
            ##################################################################

            if self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY and pv < float(
                self.settings.min_pv_kwh
            ):
                pv = 0.0

            ##################################################################
            # PV kan nooit meer zijn dan de maximale
            # laadcapaciteit van het beschikbare uur.
            ##################################################################

            if pv > max_charge_energy:
                pv = max_charge_energy

            hour.usable_pv = float(pv)

            self.logger.debug(
                f"PV BEREKENING: "
                f"{hour.start:%d-%m %H:%M}"
                f"-{hour.end:%H:%M}"
                f" | pv_estimate="
                f"{original_pv:.3f} kWh"
                f" | duur="
                f"{duration:.3f} uur"
                f" | max_laden="
                f"{max_charge_energy:.3f} kWh"
                f" | usable_pv="
                f"{hour.usable_pv:.3f} kWh"
            )

        return hours

    ##########################################################################
    # Uur voorbereiden
    ##########################################################################

    def _prepare_hour(
        self,
        hour: Hour,
    ) -> None:

        hour.free_energy = 0.0

        hour.paid_energy = 0.0

        hour.charge_energy = 0.0

        hour.effective_price = 999.0

        hour.score = 999.0

        hour.selected = False

        hour.reason = ""

        max_power = float(self.settings.max_charge_power_kw)

        if max_power > ABSOLUTE_MAX_POWER_KW:
            max_power = ABSOLUTE_MAX_POWER_KW

        duration = self._hour_duration(hour)

        if duration <= 0:
            return

        max_charge_energy = max_power * duration

        ######################################################################
        # Gratis PV
        ######################################################################

        if self.settings.solar_is_free:
            usable_pv = float(hour.usable_pv)

            if usable_pv < 0:
                usable_pv = 0.0

            if usable_pv > max_charge_energy:
                usable_pv = max_charge_energy

            hour.free_energy = float(usable_pv)

        ######################################################################
        # Betaalde capaciteit
        ######################################################################

        hour.paid_energy = max(
            0.0,
            max_charge_energy - float(hour.free_energy),
        )

        hour.charge_energy = float(hour.free_energy) + float(hour.paid_energy)

        ######################################################################
        # Effectieve prijs
        ######################################################################

        if hour.charge_energy > 0:
            paid_cost = float(hour.paid_energy) * float(hour.price)

            hour.effective_price = paid_cost / float(hour.charge_energy)

        else:
            hour.effective_price = float(hour.price)

        hour.score = float(hour.effective_price)

    ##########################################################################
    # Totale beschikbare energie
    ##########################################################################

    def _total_available_energy(
        self,
        hours: list[Hour],
    ) -> float:
        """
        Ruwe, snelle bovengrens op wat er in totaal fysiek
        inpasbaar is (ongeacht prijs of fasewisselbudget) --
        uitsluitend voor een vroege, informatieve
        haalbaarheidswaarschuwing. De exacte, gezaghebbende controle
        gebeurt in _optimize_hours() zelf.
        """

        total = 0.0

        maximum_power = self._maximum_power_for_phases(3)

        for hour in hours:
            duration = self._hour_duration(hour)

            if duration > 0:
                total += maximum_power * duration

        return float(total)

    ##########################################################################
    # Haalbaarheid
    ##########################################################################

    def _check_feasibility(
        self,
        hours: list[Hour],
    ) -> None:

        available = self._total_available_energy(hours)

        needed = float(self.settings.energy_needed_kwh)

        if available < needed:
            self.logger.warning(
                "Laadopdracht kan niet volledig "
                "worden ingepland. "
                f"Benodigd: {needed:.2f} kWh, "
                f"maximaal beschikbaar: "
                f"{available:.2f} kWh."
            )

    ##########################################################################
    # Maximum vermogen per faseconfiguratie
    ##########################################################################

    def _maximum_power_for_phases(
        self,
        phases: int,
    ) -> float:

        configured_max = float(self.settings.max_charge_power_kw)

        ######################################################################
        # Absolute veiligheidsbegrenzing.
        ######################################################################

        if configured_max > ABSOLUTE_MAX_POWER_KW:
            configured_max = ABSOLUTE_MAX_POWER_KW

        ######################################################################
        # 3 fasen
        ######################################################################

        if phases == 3:
            return float(
                min(
                    MAX_POWER_3PH_KW,
                    configured_max,
                )
            )

        ######################################################################
        # 1 fase
        ######################################################################

        return float(
            min(
                MAX_POWER_1PH_KW,
                configured_max,
            )
        )

    ##########################################################################
    # Capaciteit en gratis energie per fase
    ##########################################################################

    def _hour_max_energy(
        self,
        hour: Hour,
        phases: int,
    ) -> float:
        """
        Maximaal leverbare energie (kWh) in dit uur bij deze
        faseconfiguratie.
        """

        duration = self._hour_duration(hour)

        if duration <= 0:
            return 0.0

        power = self._maximum_power_for_phases(phases)

        return float(power * duration)

    ##########################################################################
    # Gezamenlijke optimalisatie: uren, fase, stroom en venster
    # tegelijk
    #
    #
    # HARDE FYSIEKE REGELS
    #
    # - Laadstroom is een geheel getal tussen MIN_CHARGE_CURRENT_A
    #   (6A) en MAX_CHARGE_CURRENT_A (16A), voor zowel 1 als 3 fasen.
    # - Een laadbeslissing voor een uur ligt ALTIJD volledig binnen
    #   [uur.start, uur.end] van dat specifieke uur -- nooit
    #   daarbuiten, want elk uur heeft zijn eigen prijs en eigen
    #   zonnevoorspelling.
    # - Laadblokken zijn hele uren. Alleen het EERSTE (als "nu" niet
    #   op een uurgrens valt) en het ALLERLAATSTE uur van de hele
    #   sessie mogen een korter venster hebben. Alle tussenliggende
    #   actieve uren gebruiken hun volledige beschikbare duur, op een
    #   vaste stroom.
    # - Geen onderbrekingen korter dan een heel uur: een uur is
    #   volledig actief of volledig inactief (behalve het
    #   allerlaatste uur, dat kan eindigen zodra het doel is
    #   bereikt).
    # - Zonne-energie wordt geacht gelijkmatig over zijn eigen uur
    #   uitgesmeerd te zijn (een voorspelling van 1 kWh voor een uur
    #   betekent 1 kW gemiddeld, het hele uur door). Bij een
    #   volledig uur (vaste stroom, hele duur) wordt daardoor altijd
    #   exact "min(voorspelling, geleverde energie)" gratis
    #   opgevangen. Alleen bij het (mogelijk kortere) allerlaatste
    #   uur wordt gratis energie EVENREDIG aan de werkelijke
    #   venstertijd berekend -- een korter venster kan dan nooit meer
    #   gratis energie claimen dan de zon in die kortere tijd
    #   daadwerkelijk kan leveren.
    #
    # OPTIMALISATIE
    #
    # Binnen deze harde regels wordt gezocht naar de combinatie van
    # actieve uren, fasen en stroomsterktes die de totale kosten
    # (betaalde energie x prijs) minimaliseert, binnen het
    # fasewisselbudget -- via dynamic programming over de
    # chronologische uren. Omdat elk "vol" uur een van slechts 11
    # discrete stroomniveaus gebruikt (geen tussenliggende
    # hoeveelheden), en alleen het allerlaatste actieve uur een
    # willekeurige (afgeronde) hoeveelheid mag hebben, wordt hier
    # gerekend met EXACTE energiehoeveelheden -- geen
    # rekenrooster/afronding meer nodig, en dus ook geen aparte
    # "afrondingsaanvulling" achteraf: de opgetelde energie komt
    # per constructie exact overeen met wat er daadwerkelijk wordt
    # ingepland.
    ##########################################################################

    def _pv_rate_kw(
        self,
        hour: Hour,
    ) -> float:
        """
        Zonne-opwek van dit uur, als constant vermogen (kW) over de
        volledige beschikbare duur van dit uur -- de basis voor de
        "gelijkmatig uitgesmeerd"-aanname.
        """

        duration = self._hour_duration(hour)

        if duration <= 0:
            return 0.0

        pv = float(hour.pv_estimate)

        if pv < 0:
            pv = 0.0

        if (
            self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY
            and pv < float(self.settings.min_pv_kwh)
        ):
            pv = 0.0

        return float(pv / duration)

    def _valid_currents(
        self,
        phases: int,
    ) -> list:
        """
        Geldige, gehele stroomsterktes (A) voor deze fase, begrensd
        door zowel de fysieke 16A-grens als het ingestelde maximale
        laadvermogen.
        """

        maximum_power = self._maximum_power_for_phases(phases)

        result = []

        for current_a in range(
            MIN_CHARGE_CURRENT_A,
            MAX_CHARGE_CURRENT_A + 1,
        ):
            power = self._actual_power_for_current(
                current_a,
                phases,
            )

            if power <= maximum_power + 0.000001:
                result.append(current_a)

        return result

    def _optimize_hours(
        self,
        hours: list[Hour],
    ) -> None:
        """
        Bepaalt in één keer, gezamenlijk: welke uren geladen worden,
        met welke fase en stroomsterkte, en welk laadvenster -- zie
        de sectie-toelichting hierboven voor de volledige regels.

        Muteert de meegegeven Hour-objecten direct: selected,
        phases, charge_current_a, charge_power_w, start, end,
        charge_energy, free_energy, paid_energy.
        """

        if not hours:
            return

        diagnostic_rss_before = _process_rss_mb()
        self.logger.debug(
            "EV Planner production DP START: "
            f"rss={diagnostic_rss_before:.1f} MB, "
            f"quarters={len(hours)}, "
            f"target={float(self.settings.energy_needed_kwh):.2f} kWh"
        )

        ######################################################################
        # Chronologische kopie
        ######################################################################

        ordered_hours = []

        for hour in hours:
            ordered_hours.append(hour)

        ordered_hours.sort(key=lambda hour: hour.start)

        count = len(ordered_hours)

        target = float(self.settings.energy_needed_kwh)

        if target <= 0:
            for hour in ordered_hours:
                hour.selected = False
                hour.charge_energy = 0.0
                hour.free_energy = 0.0
                hour.paid_energy = 0.0
                hour.charge_power_w = 0.0
                hour.charge_current_a = 0.0

            return

        ######################################################################
        # Max fasewisselingen, met harde bovengrens.
        ######################################################################

        max_switches = int(self.settings.max_phase_switches)

        if max_switches < 0:
            max_switches = 0

        if max_switches > count - 1:
            max_switches = max(
                0,
                count - 1,
            )

        if max_switches > MAX_PHASE_SWITCHES_HARD_CAP:
            self.logger.warning(
                "Maximaal aantal fasewisselingen begrensd van "
                f"{max_switches} naar "
                f"{MAX_PHASE_SWITCHES_HARD_CAP} om de planner "
                "responsief te houden."
            )

            max_switches = MAX_PHASE_SWITCHES_HARD_CAP

        max_price = float(self.settings.max_price)

        ######################################################################
        # Per uur, per fase: beschikbare duur, zonvermogen, geldige
        # stroomsterktes en de daarbij horende volledige-uur
        # hoeveelheden (bedrag, gratis, betaald).
        ######################################################################

        durations = []

        pv_rates = []

        prices = []

        # full_options[(index, phase)] = list van
        # (current_a, amount_kwh, amount_key, free_kwh, paid_kwh), oplopend van
        # laag naar hoog vermogen.
        full_options = {}

        valid_currents_by_phase = {
            1: self._valid_currents(1),
            3: self._valid_currents(3),
        }

        power_by_phase_current = {
            1: {},
            3: {},
        }

        for phase in (1, 3):
            for current_a in valid_currents_by_phase[phase]:
                power_by_phase_current[phase][current_a] = (
                    self._actual_power_for_current(current_a, phase)
                )

        for index in range(count):
            hour = ordered_hours[index]

            duration = self._hour_duration(hour)

            durations.append(duration)

            pv_rates.append(self._pv_rate_kw(hour))

            prices.append(float(hour.price))

            for phase in (1, 3):
                options = []

                if duration > 0:
                    for current_a in valid_currents_by_phase[phase]:
                        power = power_by_phase_current[phase][current_a]

                        amount = float(power * duration)

                        free = float(
                            min(
                                pv_rates[index],
                                power,
                            )
                            * duration
                        )

                        paid = amount - free

                        if paid < 0:
                            paid = 0.0

                        ##########################################################
                        # max_price: een uur boven de maximumprijs
                        # mag als VOL uur alleen worden gebruikt als
                        # de volledige hoeveelheid gratis is (dus de
                        # gekozen stroom niet boven het
                        # zonvermogen ligt).
                        ##########################################################

                        if prices[index] > max_price and paid > 0.000001:
                            continue

                        amount_key = int(
                            amount * 1000000000.0 + 0.5
                        )

                        options.append(
                            (
                                current_a,
                                amount,
                                amount_key,
                                free,
                                paid,
                            )
                        )

                full_options[(index, phase)] = options

        ######################################################################
        # Maximumprijs-bewuste bovengrens voor de resterende energie.
        #
        # Als een uur duurder is dan max_price, mag alleen de gratis PV
        # uit dat uur worden gebruikt. Voor betaalbare uren blijft de
        # volledige fysieke capaciteit beschikbaar.
        #
        # Deze bovengrens is alleen een noodzakelijke voorwaarde voor een
        # COMPLETE planning. Als de target-energie binnen de maximumprijs
        # niet volledig haalbaar is, schakelen we de target-pruning uit
        # zodat de DP juist de maximaal haalbare PARTIËLE planning kan
        # teruggeven.
        ######################################################################

        suffix_max_price_energy = [0.0] * (count + 1)

        for index in range(count - 1, -1, -1):
            hour = ordered_hours[index]

            if float(hour.price) <= max_price + 0.000001:
                eligible_energy = self._hour_max_energy(hour, 3)
            else:
                eligible_energy = float(hour.free_energy)

            suffix_max_price_energy[index] = (
                suffix_max_price_energy[index + 1]
                + max(0.0, eligible_energy)
            )

        target_reachable_with_max_price = (
            suffix_max_price_energy[0] >= target - 0.000001
        )

        ######################################################################
        # Dynamic programming.
        #
        # state key: (fase, wisselingen) -- fase 0 = "nog geen
        # enkel actief uur gehad".
        #
        # layers[i][state] = { energie (exact, afgerond voor de
        # dict-sleutel): (kosten, ouder_state, ouder_energie, actie) }
        #
        # actie = ("skip",)
        #       | ("full", fase, stroom_A)
        #       | ("finish", fase, stroom_A, bedrag_kwh)
        ######################################################################

        NONE_PHASE = 0

        # DP energy keys are integer nano-kWh. Convert back to kWh only
        # when a real amount is needed.
        ENERGY_SCALE = 1000000000.0

        # Compact integer state id:
        # 0 = no active phase yet
        # odd ids = 1-phase with switch count encoded
        # even ids = 3-phase with switch count encoded.
        start_state = 0

        # State ids are tiny (0..18 with the hard cap of 8 switches).
        # Decode phase/switch count once instead of doing integer
        # arithmetic for every DP state in every layer.
        max_state_id = 2 + (max_switches * 2)
        phase_by_state = [NONE_PHASE] * (max_state_id + 1)
        switches_by_state = [0] * (max_state_id + 1)

        for switches in range(max_switches + 1):
            phase_by_state[1 + (switches * 2)] = 1
            phase_by_state[2 + (switches * 2)] = 3
            switches_by_state[1 + (switches * 2)] = switches
            switches_by_state[2 + (switches * 2)] = switches

        ######################################################################
        # Directe predecessor-DP.
        #
        # Checkpoint-reconstructie is bewust volledig verwijderd.
        #
        # De DP bewaart nu tijdens de eerste en enige optimalisatierun
        # rechtstreeks de predecessor voor iedere overlevende candidate.
        # Daardoor is achteraf geen tweede DP-run nodig om het plan te
        # reconstrueren.
        #
        # Dit gebruikt meer geheugen dan de checkpointvariant, maar voorkomt
        # de extra rekentijd en -- belangrijker -- dat de reconstructie een
        # tweede keer exact dezelfde complexe DP moet uitvoeren.
        ######################################################################

        layer_profile = []

        # Fine-grained hot-loop diagnostics. These counters are intentionally
        # kept local to the profiling branch so production code is unchanged.
        profile = {
            "states": 0,
            "states_pruned": 0,
            "skip_attempts": 0,
            "skip_updates": 0,
            "full_attempts": 0,
            "full_breaks_remaining": 0,
            "full_candidates_evaluated": 0,
            "full_no_update": 0,
            "full_updates": 0,
            "finish_attempts": 0,
            "finish_updates": 0,
            "state_iteration_ms": 0.0,
            "full_loop_ms": 0.0,
            "finish_ms": 0.0,
            "dict_lookup_ms": 0.0,
            "parent_write_ms": 0.0,
        }

        def advance_layer(
            prev_layer,
            index,
        ):
            layer_start = perf_counter()
            next_layer = {}
            parent_layer = {}

            price = prices[index]
            options_by_phase = (
                None,
                full_options[(index, 1)],
                None,
                full_options[(index, 3)],
            )

            for state_id, energies in prev_layer.items():
                state_iteration_start = perf_counter()
                phase_prev = phase_by_state[state_id]
                switches_prev = switches_by_state[state_id]

                phase_data = []

                for phase in (1, 3):
                    if phase_prev == NONE_PHASE:
                        switches_new = 0
                    elif phase_prev == phase:
                        switches_new = switches_prev
                    else:
                        switches_new = switches_prev + 1

                    if switches_new > max_switches:
                        continue

                    if phase == 1:
                        out_state = 1 + (switches_new * 2)
                    else:
                        out_state = 2 + (switches_new * 2)

                    out_bucket = next_layer.get(out_state)
                    if out_bucket is None:
                        out_bucket = {}
                        next_layer[out_state] = out_bucket

                    phase_data.append(
                        (
                            phase,
                            out_state,
                            out_bucket,
                            options_by_phase[phase],
                        )
                    )

                for energy_k, cost in energies.items():
                    profile["states"] += 1
                    energy = float(energy_k) / ENERGY_SCALE
                    remaining = target - energy

                    if (
                        target_reachable_with_max_price
                        and energy + suffix_max_price_energy[index]
                        < target - 0.000001
                    ):
                        profile["states_pruned"] += 1
                        continue

                    bucket = next_layer.get(state_id)
                    if bucket is None:
                        bucket = {}
                        next_layer[state_id] = bucket

                    profile["skip_attempts"] += 1
                    existing = bucket.get(energy_k)
                    if existing is None or cost < existing:
                        profile["skip_updates"] += 1
                        bucket[energy_k] = cost
                        parent_bucket = parent_layer.get(state_id)
                        if parent_bucket is None:
                            parent_bucket = {}
                            parent_layer[state_id] = parent_bucket
                        parent_bucket[energy_k] = (
                            energy_k << PARENT_ACTION_SHIFT
                        ) | (state_id << 8)

                    if remaining <= 0.000001:
                        continue

                    for (
                        phase,
                        out_state,
                        out_bucket,
                        options,
                    ) in phase_data:
                        # Volledig uur.
                        full_loop_start = perf_counter()
                        for (
                            current_a,
                            amount,
                            amount_key,
                            free,
                            paid,
                        ) in options:
                            profile["full_attempts"] += 1
                            if amount > remaining + 0.000001:
                                profile["full_breaks_remaining"] += 1
                                break

                            profile["full_candidates_evaluated"] += 1
                            new_energy_key = energy_k + amount_key

                            new_cost = (
                                cost
                                + paid * price
                                - CURRENT_TIEBREAK_EPSILON * current_a
                            )

                            existing = out_bucket.get(new_energy_key)
                            if existing is None or new_cost < existing:
                                profile["full_updates"] += 1
                                out_bucket[new_energy_key] = new_cost
                                parent_bucket = parent_layer.get(out_state)
                                if parent_bucket is None:
                                    parent_bucket = {}
                                    parent_layer[out_state] = parent_bucket
                                action_code = (
                                    PARENT_ACTION_FULL
                                    | (PARENT_PHASE_3 if phase == 3 else 0)
                                    | int(current_a)
                                )
                                parent_bucket[new_energy_key] = (
                                    (energy_k << PARENT_ACTION_SHIFT)
                                    | (state_id << PARENT_STATE_SHIFT)
                                    | action_code
                                )
                            else:
                                profile["full_no_update"] += 1

                        profile["full_loop_ms"] += (perf_counter() - full_loop_start) * 1000.0

                        # Finish.
                        finish_start = perf_counter()
                        duration = durations[index]

                        if duration <= 0:
                            continue

                        pv_rate = pv_rates[index]
                        price_ok_currents = valid_currents_by_phase[phase]

                        if not price_ok_currents:
                            continue

                        minimum_current = int(price_ok_currents[0])
                        maximum_current = int(price_ok_currents[-1])

                        required_current = int(
                            ceil(
                                (
                                    (remaining - 0.000001)
                                    * 1000.0
                                )
                                / (
                                    VOLTAGE_V
                                    * float(phase)
                                    * duration
                                )
                            )
                        )

                        if required_current < minimum_current:
                            required_current = minimum_current

                        if required_current > maximum_current:
                            continue

                        best_current = required_current
                        power = power_by_phase_current[phase][best_current]

                        if price > max_price:
                            free_energy = min(
                                pv_rate,
                                power,
                            ) * duration

                            if free_energy < power * duration - 0.000001:
                                continue

                        finish_duration = remaining / power

                        if finish_duration > duration:
                            finish_duration = duration

                        finish_amount = power * finish_duration

                        finish_free = float(
                            min(
                                pv_rate,
                                power,
                            )
                            * finish_duration
                        )

                        finish_paid = finish_amount - finish_free

                        if finish_paid < 0:
                            finish_paid = 0.0

                        new_energy = (
                            float(energy_k) / ENERGY_SCALE
                        ) + finish_amount

                        new_cost = cost + finish_paid * prices[index]

                        new_key = int(
                            new_energy * ENERGY_SCALE + 0.5
                        )

                        profile["finish_attempts"] += 1
                        existing = out_bucket.get(new_key)
                        if existing is None or new_cost < existing:
                            profile["finish_updates"] += 1
                            out_bucket[new_key] = new_cost
                            parent_bucket = parent_layer.get(out_state)
                            if parent_bucket is None:
                                parent_bucket = {}
                                parent_layer[out_state] = parent_bucket
                            action_code = (
                                PARENT_ACTION_FINISH
                                | (PARENT_PHASE_3 if phase == 3 else 0)
                                | int(best_current)
                            )
                            parent_bucket[new_key] = (
                                (energy_k << PARENT_ACTION_SHIFT)
                                | (state_id << 8)
                                | action_code
                            )

                        profile["finish_ms"] += (perf_counter() - finish_start) * 1000.0

                profile["state_iteration_ms"] += (
                    perf_counter() - state_iteration_start
                ) * 1000.0

            layer_elapsed_ms = (perf_counter() - layer_start) * 1000.0
            layer_state_count = sum(
                len(bucket)
                for bucket in next_layer.values()
            )
            layer_profile.append(
                (
                    index,
                    layer_elapsed_ms,
                    layer_state_count,
                )
            )

            return next_layer, parent_layer

        start_layer = {
            start_state: {
                0: 0.0,
            }
        }

        current_layer = start_layer
        parent_layers = []

        for index in range(count):
            current_layer, parent_layer = advance_layer(
                current_layer,
                index,
            )
            parent_layers.append(parent_layer)

            if (
                (index + 1) % MEMORY_DIAGNOSTIC_INTERVAL == 0
                or index == count - 1
            ):
                rss = _process_rss_mb()
                state_count = sum(
                    len(bucket)
                    for bucket in current_layer.values()
                )
                self.logger.info(
                    "EV Planner DP PERF quarter="
                    f"{index + 1}/{count}: "
                    f"layer={layer_profile[-1][1]:.1f} ms, "
                    f"states={state_count}, "
                    f"rss={rss:.1f} MB"
                )

        self.logger.info(
            "EV Planner DP HOTLOOP PERF: "
            f"states={profile['states']}, "
            f"pruned={profile['states_pruned']}, "
            f"skip={profile['skip_attempts']}/{profile['skip_updates']}, "
            f"full={profile['full_attempts']}/"
            f"{profile['full_candidates_evaluated']}/"
            f"{profile['full_updates']}/"
            f"{profile['full_no_update']}/"
            f"break={profile['full_breaks_remaining']}, "
            f"finish={profile['finish_attempts']}/{profile['finish_updates']}, "
            f"state_loop={profile['state_iteration_ms']:.1f} ms, "
            f"full_loop={profile['full_loop_ms']:.1f} ms, "
            f"finish={profile['finish_ms']:.1f} ms"
        )

        last_layer = current_layer

        ######################################################################
        # Beste eindstate: maximale energie, dan minimale kosten,
        # dan minimale fasewisselingen.
        ######################################################################

        best_state = None

        best_energy_key = None

        best_cost = None

        best_switches = None

        for state_id, energies in last_layer.items():
            if state_id == 0:
                switches = 0
            else:
                switches = (state_id - 1) // 2

            for energy_k, cost in energies.items():
                if (
                    best_state is None
                    or energy_k > best_energy_key
                    or (
                        energy_k == best_energy_key and cost < best_cost
                    )
                    or (
                        energy_k == best_energy_key
                        and abs(cost - best_cost) <= 0.000001
                        and switches < best_switches
                    )
                ):
                    best_state = state_id
                    best_energy_key = energy_k
                    best_cost = cost
                    best_switches = switches

        diagnostic_rss_after = _process_rss_mb()

        if layer_profile:
            slowest_layer = max(
                layer_profile,
                key=lambda item: item[1],
            )
            total_layer_ms = sum(
                item[1]
                for item in layer_profile
            )
            self.logger.info(
                "EV Planner DP PERF SUMMARY: "
                f"layers={len(layer_profile)}, "
                f"total_layers={total_layer_ms:.1f} ms, "
                f"slowest_quarter={slowest_layer[0] + 1}, "
                f"slowest_layer={slowest_layer[1]:.1f} ms, "
                f"slowest_states={slowest_layer[2]}, "
                f"rss_delta={diagnostic_rss_after - diagnostic_rss_before:.1f} MB"
            )

        self.logger.debug(
            "EV Planner production DP END: "
            f"rss={diagnostic_rss_after:.1f} MB, "
            f"delta={diagnostic_rss_after - diagnostic_rss_before:.1f} MB, "
            f"parent_layers={len(parent_layers)}"
        )

        if best_state is None:
            for hour in ordered_hours:
                hour.selected = False
                hour.charge_energy = 0.0
                hour.free_energy = 0.0
                hour.paid_energy = 0.0
                hour.charge_power_w = 0.0
                hour.charge_current_a = 0.0

            self.logger.warning("Geen enkele haalbare laadplanning gevonden.")

            return

        if float(best_energy_key) / ENERGY_SCALE < target - 0.001:
            self.logger.warning(
                "Laadopdracht kan niet volledig worden ingepland. "
                f"Benodigd: {target:.2f} kWh, maximaal haalbaar: "
                f"{best_energy_key:.2f} kWh binnen het "
                f"fasewisselbudget ({max_switches}) en de "
                f"maximumprijs (EUR{max_price:.3f})."
            )

        ######################################################################
        # Directe reconstructie.
        #
        # Iedere parent_layer hoort bij precies één DP-overgang. Er wordt
        # niets opnieuw berekend. We volgen de parent-keten vanaf de gekozen
        # eindstate terug naar de start.
        ######################################################################

        actions = [None] * count

        current_state = best_state
        current_energy_key = best_energy_key

        for index in range(count - 1, -1, -1):
            parent_layer = parent_layers[index]
            parent = parent_layer[current_state][current_energy_key]

            parent_energy_key = parent >> PARENT_ACTION_SHIFT
            parent_state = (parent >> PARENT_STATE_SHIFT) & PARENT_STATE_MASK
            action_code = parent & PARENT_ACTION_MASK

            if action_code == 0:
                action = ("skip",)
            else:
                phase = 3 if action_code & PARENT_PHASE_3 else 1
                current_a = action_code & PARENT_CURRENT_MASK
                if action_code & PARENT_ACTION_FINISH:
                    finish_amount = (
                        float(current_energy_key - parent_energy_key)
                        / ENERGY_SCALE
                    )
                    action = ("finish", phase, current_a, finish_amount)
                else:
                    action = ("full", phase, current_a)

            actions[index] = action

            current_state = parent_state
            current_energy_key = parent_energy_key

        if current_state != start_state or current_energy_key != 0:
            raise ValueError(
                "DP-reconstructie eindigt niet bij de startstate."
            )

        ######################################################################
        # Acties toepassen op de Hour-objecten.
        #
        # "full": volledig beschikbare duur van dit uur, vaste
        # stroom -- gegarandeerd binnen [uur.start, uur.end].
        #
        # "finish": mag gecomprimeerd zijn; venster wordt aan het
        # BEGIN van het uur geplaatst (laden vanaf het moment dat
        # het uur begint, tot het doel is bereikt) zodat het, als er
        # een actief uur aan voorafgaat, daar naadloos op aansluit
        # -- het eigen begin van dit uur is namelijk gelijk aan het
        # eind van het voorgaande uur. Blijft per constructie binnen
        # [uur.start, uur.end], want finish_duration <= duration
        # hierboven.
        ######################################################################

        for index in range(count):
            hour = ordered_hours[index]

            action = actions[index]

            if action is None or action[0] == "skip":
                hour.selected = False
                hour.charge_energy = 0.0
                hour.free_energy = 0.0
                hour.paid_energy = 0.0
                hour.charge_power_w = 0.0
                hour.charge_current_a = 0.0

                continue

            original_start = hour.start

            original_end = hour.end

            if action[0] == "full":
                _tag, phase, current_a = action

                power = self._actual_power_for_current(
                    current_a,
                    phase,
                )

                duration = durations[index]

                amount = float(power * duration)

                free = float(
                    min(
                        pv_rates[index],
                        power,
                    )
                    * duration
                )

                paid = amount - free

                if paid < 0:
                    paid = 0.0

                hour.start = original_start

                hour.end = original_end

            else:
                _tag, phase, current_a, amount = action

                power = self._actual_power_for_current(
                    current_a,
                    phase,
                )

                finish_duration = amount / power

                free = float(
                    min(
                        pv_rates[index],
                        power,
                    )
                    * finish_duration
                )

                paid = amount - free

                if paid < 0:
                    paid = 0.0

                hour.start = original_start

                hour.end = original_start + self._duration_to_timedelta(finish_duration)

                ################################################################
                # Harde veiligheidsklem: kan door afrondingen op
                # microseconden nooit na het eigen uureinde
                # uitkomen.
                ################################################################

                if hour.end > original_end:
                    hour.end = original_end

            hour.selected = True

            hour.phases = int(phase)

            hour.charge_current_a = float(current_a)

            hour.charge_power_w = float(power * 1000.0)

            hour.charge_energy = float(amount)

            hour.free_energy = float(free)

            hour.paid_energy = float(paid)

        if best_state == 0:
            best_phase = NONE_PHASE
            best_switches = 0
        else:
            best_phase = 1 if best_state & 1 else 3
            best_switches = (best_state - 1) // 2

        self.logger.debug(
            "GEZAMENLIJKE OPTIMALISATIE: "
            f"{float(best_energy_key) / ENERGY_SCALE:.3f}/{target:.3f} kWh, "
            f"kosten EUR{best_cost:.4f}, "
            f"eindfase={best_phase}, "
            f"wisselingen={best_switches}"
        )

    ##########################################################################
    # Werkelijk laadvermogen
    ##########################################################################

    def _actual_power_for_current(
        self,
        current_a: int,
        phases: int,
    ) -> float:

        if current_a <= 0:
            return 0.0

        if phases <= 0:
            return 0.0

        power_kw = VOLTAGE_V * float(current_a) * float(phases) / 1000.0

        return float(power_kw)

    ##########################################################################
    # Tijdduur naar timedelta
    ##########################################################################

    def _duration_to_timedelta(
        self,
        duration_hours: float,
    ) -> timedelta:

        seconds = float(duration_hours) * 3600.0

        return timedelta(seconds=seconds)

    ##########################################################################
    # Decisions bouwen
    ##########################################################################

    def _build_decisions(
        self,
        hours: list[Hour],
    ) -> list[ChargingDecision]:
        """
        Bouwt de beslissingslijst rechtstreeks uit de Hour-velden.

        _optimize_hours() heeft charge_power_w / charge_current_a /
        phases al direct op elk Hour-object gezet -- dit is nu de
        enige bron van waarheid, er is geen aparte
        "charging_settings"-tussenstap meer.
        """

        decisions = []

        for index in range(len(hours)):
            hour = hours[index]

            if not hour.selected:
                continue

            cost = float(hour.paid_energy) * float(hour.price)

            charge_power_kw = float(hour.charge_power_w) / 1000.0

            charge_current_a = int(hour.charge_current_a)

            phases = int(hour.phases)

            decision = ChargingDecision(
                hour=hour,
                energy_kwh=float(hour.charge_energy),
                free_energy_kwh=float(hour.free_energy),
                paid_energy_kwh=float(hour.paid_energy),
                price=float(hour.price),
                cost=float(cost),
                selected=True,
                reason=str(hour.reason),
                charge_power_kw=float(charge_power_kw),
                charge_current_a=int(charge_current_a),
                phases=int(phases),
            )

            decisions.append(decision)

        ######################################################################
        # Altijd chronologisch
        ######################################################################

        decisions.sort(key=lambda decision: decision.hour.start)

        return decisions

    ##########################################################################
    # ChargingPlan bouwen
    ##########################################################################

    def _build_plan(
        self,
        selected: list[Hour],
    ) -> ChargingPlan:

        decisions = self._build_decisions(selected)

        energy_planned = 0.0

        free_energy = 0.0

        paid_energy = 0.0

        estimated_cost = 0.0

        for decision in decisions:
            energy_planned += float(decision.energy_kwh)

            free_energy += float(decision.free_energy_kwh)

            paid_energy += float(decision.paid_energy_kwh)

            estimated_cost += float(decision.cost)

        ######################################################################
        # Ontbrekende energie
        ######################################################################

        energy_needed = float(self.settings.energy_needed_kwh)

        missing_energy = max(
            0.0,
            energy_needed - energy_planned,
        )

        ######################################################################
        # Compleet
        #
        # De toegestane afwijking is nu zeer klein (1e-6): sinds de
        # herbouw werken alle berekeningen met EXACTE hoeveelheden
        # (geen rekenrooster meer), dus energy_planned komt per
        # constructie tot op float-afrondingsniveau overeen met wat
        # daadwerkelijk is ingepland. Een grotere afwijking hier zou
        # een echt probleem verbergen in plaats van floating-point
        # ruis.
        ######################################################################

        complete = energy_planned + 0.000001 >= energy_needed

        return ChargingPlan(
            decisions=decisions,
            energy_needed_kwh=(energy_needed),
            energy_planned_kwh=(energy_planned),
            missing_energy_kwh=(missing_energy),
            free_energy_kwh=(free_energy),
            paid_energy_kwh=(paid_energy),
            estimated_cost=(estimated_cost),
            complete=complete,
            departure_time=(self.settings.departure_time),
            max_price=float(self.settings.max_price),
        )

    ##########################################################################
    # Definitieve plancontrole
    ##########################################################################

    def _validate_final_plan(
        self,
        plan: ChargingPlan,
    ) -> None:
        """
        Strenge eindcontrole.

        Sinds de herbouw werkt de planner met EXACTE hoeveelheden
        (geen rekenrooster meer), dus de toleranties hier zijn strak
        (1e-6) -- een grotere afwijking wijst op een echte fout, niet
        op afrondingsruis. Naast de eerdere controles worden nu ook
        expliciet gecontroleerd: de maximumprijs, het
        fasewisselbudget, en dat een laadbeslissing nooit buiten
        zijn eigen oorspronkelijke uur valt.
        """

        TOLERANCE = 0.000001

        ######################################################################
        # Totale energie mag niet boven de benodigde energie komen.
        ######################################################################

        needed = float(plan.energy_needed_kwh)

        planned = float(plan.energy_planned_kwh)

        if planned > needed + TOLERANCE:
            raise ValueError(
                "PLANCONTROLE: geplande energie is groter "
                f"dan benodigd: {planned:.6f} > {needed:.6f} kWh"
            )

        ######################################################################
        # Fasewisselbudget: tel het werkelijke aantal wisselingen in
        # het uiteindelijke plan en vergelijk met de instelling.
        ######################################################################

        max_switches = int(self.settings.max_phase_switches)

        if max_switches < 0:
            max_switches = 0

        switches = 0

        previous_phase = None

        for decision in sorted(
            plan.decisions,
            key=lambda decision: decision.hour.start,
        ):
            phase = int(decision.phases)

            if previous_phase is not None and phase != previous_phase:
                switches += 1

            previous_phase = phase

        if switches > max_switches:
            raise ValueError(
                "PLANCONTROLE: aantal fasewisselingen "
                f"({switches}) overschrijdt het budget "
                f"({max_switches})."
            )

        ######################################################################
        # Beslissingen controleren.
        ######################################################################

        max_price = float(self.settings.max_price)

        for decision in plan.decisions:
            phases = int(decision.phases)

            current = int(decision.charge_current_a)

            power = float(decision.charge_power_kw)

            hour = decision.hour

            ##################################################################
            # Geldige faseconfiguratie
            ##################################################################

            if phases != 1 and phases != 3:
                raise ValueError("Ongeldige faseconfiguratie in laadplan.")

            ##################################################################
            # Geldige, gehele laadstroom
            ##################################################################

            if current < MIN_CHARGE_CURRENT_A:
                raise ValueError(
                    "Laadstroom lager dan minimale "
                    f"{MIN_CHARGE_CURRENT_A} A: {current} A."
                )

            if current > MAX_CHARGE_CURRENT_A:
                raise ValueError(
                    "Laadstroom hoger dan maximale "
                    f"{MAX_CHARGE_CURRENT_A} A: {current} A."
                )

            ##################################################################
            # Werkelijk vermogen controleren
            ##################################################################

            calculated_power = self._actual_power_for_current(
                current,
                phases,
            )

            if abs(calculated_power - power) > TOLERANCE:
                raise ValueError(
                    "PLANCONTROLE: vermogen komt niet exact "
                    "overeen met stroom/fasen. "
                    f"{power:.6f} kW versus "
                    f"{calculated_power:.6f} kW."
                )

            ##################################################################
            # Configuratiemaximum
            ##################################################################

            maximum_power = self._maximum_power_for_phases(phases)

            if power > maximum_power + TOLERANCE:
                raise ValueError(
                    "Laadvermogen overschrijdt technisch maximum: "
                    f"{power:.6f} > "
                    f"{maximum_power:.6f} kW."
                )

            ##################################################################
            # Absolute maximum
            ##################################################################

            if power > ABSOLUTE_MAX_POWER_KW + TOLERANCE:
                raise ValueError(
                    "Laadvermogen overschrijdt absolute "
                    f"grens van {ABSOLUTE_MAX_POWER_KW:.2f} kW."
                )

            ##################################################################
            # 1-fase harde grens
            ##################################################################

            if phases == 1 and power > MAX_POWER_1PH_KW + TOLERANCE:
                raise ValueError(
                    f"1-fase laadvermogen overschrijdt {MAX_POWER_1PH_KW:.2f} kW."
                )

            ##################################################################
            # PV mag nooit boven Solcast uitkomen.
            ##################################################################

            if float(decision.free_energy_kwh) > float(hour.usable_pv) + TOLERANCE:
                raise ValueError("Gratis PV-energie overschrijdt beschikbare PV.")

            ##################################################################
            # Gratis + betaald moet exact de laadenergie vormen.
            ##################################################################

            calculated_energy = float(decision.free_energy_kwh) + float(
                decision.paid_energy_kwh
            )

            if abs(calculated_energy - float(decision.energy_kwh)) > TOLERANCE:
                raise ValueError(
                    "Gratis + betaalde energie komt niet overeen "
                    "met de totale laadenergie."
                )

            ##################################################################
            # Maximumprijs: boven de maximumprijs mag een uur geen
            # betaalde energie bevatten.
            ##################################################################

            if (
                float(hour.price) > max_price + TOLERANCE
                and float(decision.paid_energy_kwh) > TOLERANCE
                and not (
                    self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY
                    and self.settings.pv_rounding == PV_ROUNDING_UP
                )
            ):
                raise ValueError(
                    "PLANCONTROLE: betaalde energie in een uur "
                    "boven de maximumprijs. "
                    f"prijs={hour.price:.4f} "
                    f"max_price={max_price:.4f} "
                    f"betaald={decision.paid_energy_kwh:.4f} kWh."
                )

            ##################################################################
            # Harde uurgrens: een laadvenster mag nooit buiten zijn
            # eigen oorspronkelijke uur vallen.
            ##################################################################

            if (
                hour.original_start is not None
                and hour.start < hour.original_start - timedelta(seconds=1)
            ):
                raise ValueError(
                    "PLANCONTROLE: laadvenster begint vóór het "
                    "eigen oorspronkelijke uur "
                    f"({hour.start} < {hour.original_start})."
                )

            if (
                hour.original_end is not None
                and hour.end > hour.original_end + timedelta(seconds=1)
            ):
                raise ValueError(
                    "PLANCONTROLE: laadvenster eindigt na het "
                    "eigen oorspronkelijke uur "
                    f"({hour.end} > {hour.original_end})."
                )

        ######################################################################
        # Hele-uur-regel: alleen het chronologisch LAATSTE actieve
        # uur mag korter zijn dan zijn eigen oorspronkelijke duur.
        ######################################################################

        ordered_decisions = sorted(
            plan.decisions,
            key=lambda decision: decision.hour.start,
        )

        for position in range(len(ordered_decisions)):
            is_last = position == len(ordered_decisions) - 1

            if is_last:
                continue

            decision = ordered_decisions[position]

            hour = decision.hour

            if hour.original_start is None or hour.original_end is None:
                continue

            own_duration = (hour.original_end - hour.original_start).total_seconds()

            actual_duration = (hour.end - hour.start).total_seconds()

            if own_duration - actual_duration > 1.0:
                raise ValueError(
                    "PLANCONTROLE: een niet-laatste uur is "
                    "korter dan zijn eigen volledige duur "
                    f"({hour.start} - dit mag alleen bij het "
                    "allerlaatste actieve uur van de sessie)."
                )

        ######################################################################
        # Plancontrole logging
        ######################################################################

        self.logger.debug(
            "PLANCONTROLE: "
            f"{planned:.3f} kWh gepland "
            f"van {needed:.3f} kWh benodigd "
            f"| wisselingen={switches}/{max_switches} "
            f"| compleet={plan.complete}"
        )

    ##########################################################################
    # Planning loggen
    ##########################################################################

    def _log_plan(
        self,
        plan: ChargingPlan,
    ) -> None:

        self.logger.debug("------------- EV PLANNING -------------")

        self.logger.debug(f"Benodigd       : {plan.energy_needed_kwh:.2f} kWh")

        self.logger.debug(f"Gepland        : {plan.energy_planned_kwh:.2f} kWh")

        self.logger.debug(f"Gratis PV      : {plan.free_energy_kwh:.2f} kWh")

        self.logger.debug(f"Betaald        : {plan.paid_energy_kwh:.2f} kWh")

        self.logger.debug(f"Geschatte kosten: €{plan.estimated_cost:.2f}")

        self.logger.debug(f"Compleet       : {plan.complete}")

        self.logger.debug(f"Vertrek        : {plan.departure_time}")

        self.logger.debug(f"Max prijs      : €{plan.max_price:.3f}/kWh")

        self.logger.debug(f"Max fasewisselingen: {self.settings.max_phase_switches}")

        self.logger.debug(
            f"Max laadvermogen: {self.settings.max_charge_power_kw:.2f} kW"
        )

        self.logger.debug(f"Min laadstroom: {MIN_CHARGE_CURRENT_A} A")

        self.logger.debug(f"Max laadstroom: {MAX_CHARGE_CURRENT_A} A")

        self.logger.debug(f"Netspanning: {VOLTAGE_V:.0f} V")

        self.logger.debug(f"Fasewisselgrens: {PHASE_CHANGE_POWER_KW:.2f} kW")

        self.logger.debug(f"1-fase maximum: {MAX_POWER_1PH_KW:.2f} kW")

        self.logger.debug(f"3-fase maximum: {MAX_POWER_3PH_KW:.2f} kW")

        ######################################################################
        # Individuele laadbeslissingen
        ######################################################################

        for decision in plan.decisions:
            duration_minutes = self._hour_duration(decision.hour) * 60.0

            self.logger.debug(
                f"{decision.hour.start:%d-%m %H:%M}"
                f" - "
                f"{decision.hour.end:%H:%M}"
                f" | "
                f"{decision.energy_kwh:.2f} kWh"
                f" | duur="
                f"{duration_minutes:.1f} min"
                f" | vermogen="
                f"{decision.charge_power_kw:.2f} kW"
                f" | stroom="
                f"{decision.charge_current_a} A"
                f" | fasen="
                f"{decision.phases}"
                f" | gratis="
                f"{decision.free_energy_kwh:.2f}"
                f" | betaald="
                f"{decision.paid_energy_kwh:.2f}"
                f" | prijs=€"
                f"{decision.price:.3f}"
                f" | kosten=€"
                f"{decision.cost:.2f}"
                f" | "
                f"{decision.reason}"
            )

        self.logger.debug("---------------------------------------")