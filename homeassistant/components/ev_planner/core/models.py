"""
models.py

Datamodellen voor EV Smart Charging Planner.

Pyscript-compatibele versie.

Belangrijk:
- Geen @property
- Geen methodes in dataclasses
- Geen __str__
- Geen generator-expressions
- Geen list/set/dict comprehensions
- Alleen eenvoudige dataclasses en standaard Python-typen

Deze module bevat uitsluitend data-objecten.
Logica hoort thuis in planner.py, prices.py, solcast.py
of andere functionele modules.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


##############################################################################
# Eén uur uit de planning
##############################################################################


@dataclass
class Hour:

    ##########################################################################
    # Basis
    ##########################################################################

    start: datetime

    end: datetime

    # Vastgelegd door create_plan() vlak voordat de planner het
    # laadvenster (start/end) mag aanpassen. Gebruikt door
    # _validate_final_plan() om hard te controleren dat een
    # laadbeslissing nooit buiten zijn eigen oorspronkelijke uur
    # valt -- los van welke waarde start/end uiteindelijk krijgen.
    original_start: Optional[datetime] = None

    original_end: Optional[datetime] = None

    ##########################################################################
    # Energieprijs
    ##########################################################################

    price: float = 0.0

    price_raw: float = 0.0

    tariff_group: str = ""

    sustainability_score: float = 0.0

    hour_index: int = -1

    ##########################################################################
    # Solcast
    ##########################################################################

    pv_estimate: float = 0.0

    pv_estimate10: float = 0.0

    pv_estimate90: float = 0.0

    usable_pv: float = 0.0

    ##########################################################################
    # Planner
    ##########################################################################

    charge_energy: float = 0.0

    free_energy: float = 0.0

    paid_energy: float = 0.0

    effective_price: float = 999.0

    score: float = 999.0

    ##########################################################################
    # Laadinstellingen
    #
    # Deze waarden beschrijven HOE er in dit uur geladen moet worden.
    #
    # charge_power_w:
    #     Gewenst laadvermogen in Watt.
    #
    # charge_current_a:
    #     Gewenste laadstroom in Ampère.
    #
    # phases:
    #     1 of 3.
    ##########################################################################

    charge_power_w: float = 0.0

    charge_current_a: float = 0.0

    phases: int = 1

    ##########################################################################
    # Resultaat
    ##########################################################################

    selected: bool = False

    reason: str = ""


##############################################################################
# Resultaat van de planner
##############################################################################


@dataclass
class PlannerResult:

    ##########################################################################
    # Uren
    ##########################################################################

    hours: List[Hour] = field(
        default_factory=list
    )

    ##########################################################################
    # Energie
    ##########################################################################

    energy_needed: float = 0.0

    expected_pv: float = 0.0

    grid_energy: float = 0.0

    ##########################################################################
    # Kosten
    ##########################################################################

    total_cost: float = 0.0

    cheapest_price: float = 999.0

    ##########################################################################
    # Haalbaarheid
    ##########################################################################

    feasible: bool = True

    missing_energy: float = 0.0

    ##########################################################################
    # Metadata
    ##########################################################################

    generated: Optional[datetime] = None

    active_now: bool = False


##############################################################################
# Runtime status
##############################################################################


@dataclass
class RuntimeStatus:

    ##########################################################################
    # Planner
    ##########################################################################

    planner_running: bool = False

    planner_enabled: bool = False

    ##########################################################################
    # Laden
    ##########################################################################

    charging_allowed: bool = False

    connected: bool = False

    charging: bool = False

    ##########################################################################
    # Laadinstellingen
    ##########################################################################

    charge_power_w: float = 0.0

    charge_current_a: float = 0.0

    phases: int = 0

    ##########################################################################
    # Modi
    ##########################################################################

    solar_only: bool = False

    smart_mode: bool = False

    ##########################################################################
    # Huidig uur
    ##########################################################################

    current_hour: Optional[Hour] = None


##############################################################################
# Planner statistieken
##############################################################################


@dataclass
class PlannerStatistics:

    ##########################################################################
    # Energie
    ##########################################################################

    total_pv: float = 0.0

    total_grid: float = 0.0

    total_energy: float = 0.0

    ##########################################################################
    # Prijzen
    ##########################################################################

    average_price: float = 0.0

    cheapest_price: float = 999.0

    most_expensive_price: float = 0.0

    ##########################################################################
    # Uren
    ##########################################################################

    selected_hours: int = 0

    skipped_hours: int = 0

    ##########################################################################
    # Kosten
    ##########################################################################

    total_cost: float = 0.0


##############################################################################
# PriceData
##############################################################################


@dataclass
class PriceData:
    """
    Alle prijsinformatie uit Zonneplan.

    Dit object bevat uitsluitend data.
    Zoek- en tel-logica staat in prices.py.
    """

    ##########################################################################
    # Uurprijzen
    ##########################################################################

    hours: List[Hour] = field(
        default_factory=list
    )

    ##########################################################################
    # Huidige prijs
    ##########################################################################

    current_price: float = 0.0

    ##########################################################################
    # Statistieken
    ##########################################################################

    cheapest_price: float = 999.0

    highest_price: float = 0.0

    average_price: float = 0.0

    ##########################################################################
    # Metadata
    ##########################################################################

    generated: Optional[datetime] = None

    valid_until: Optional[datetime] = None


##############################################################################
# SolcastData
##############################################################################


@dataclass
class SolcastData:

    ##########################################################################
    # Uurvoorspellingen
    ##########################################################################

    hours: List[Hour] = field(
        default_factory=list
    )

    ##########################################################################
    # Metadata
    ##########################################################################

    generated: Optional[datetime] = None

    valid_until: Optional[datetime] = None

    ##########################################################################
    # Totalen
    ##########################################################################

    total_today: float = 0.0

    total_tomorrow: float = 0.0