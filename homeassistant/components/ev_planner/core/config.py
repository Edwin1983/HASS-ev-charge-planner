"""
config.py

Centrale configuratie voor de EV Planner.

De EV Planner is uitsluitend verantwoordelijk voor:

- het lezen van plannerinstellingen;
- het lezen van energieprijzen;
- het lezen van PV-voorspellingen;
- het berekenen van een laadplanning;
- het beschikbaar stellen van plannerresultaten.

De EV Planner bestuurt geen laadpaal.

Home Assistant is verantwoordelijk voor:

- het periodiek aanroepen van de planner;
- bepalen wanneer opnieuw gepland wordt;
- detecteren of een auto aangesloten is;
- laden aan/uit;
- laadstroom;
- fasekeuze.
"""


##############################################################################
# Home Assistant entities
#
# LET OP: dit is uitsluitend nog een fallback-default voor PriceReader /
# SolcastReader wanneer die zonder expliciete entity_id worden
# aangemaakt (bijv. in tests). De daadwerkelijk gebruikte entity-ID's
# komen uit de ConfigEntry (config flow / options flow) en worden
# opgebouwd in core/ev_planner.py, met de standaardwaarden uit
# ../const.py. Wijzig hier dus niets als je entity-ID's wil aanpassen
# via de UI — gebruik "Configureren" bij de EV Planner-integratie.
##############################################################################

ENTITIES = {

    # ----------------------------------------------------------------------
    # Electricity prices
    # ----------------------------------------------------------------------

    "prices":
        "sensor.zonneplan_current_electricity_tariff",


    # ----------------------------------------------------------------------
    # Solcast
    # ----------------------------------------------------------------------

    "solcast_today":
        "sensor.solcast_pv_forecast_forecast_today",

    "solcast_tomorrow":
        "sensor.solcast_pv_forecast_forecast_tomorrow",
}


##############################################################################
# Planner laadmodel
##############################################################################

# Minimale laadstroom waarmee de planner rekent.
#
# Dit is GEEN opdracht aan de laadpaal.
MIN_CURRENT = 6


# Maximale 1-fase laadstroom waarmee de planner rekent.
#
# Dit is GEEN opdracht aan de laadpaal.
MAX_CURRENT_1PH = 16


# Nominale netspanning voor de vermogensberekening.
VOLTAGE = 230


# Maximaal vermogen bij 1 fase / 16 A.
MAX_POWER_1PH = (
    VOLTAGE * MAX_CURRENT_1PH
) / 1000

# Maximaal vermogen bij 3 fase / 16 A.
MAX_POWER_3PH = (
    VOLTAGE * MAX_CURRENT_1PH * 3
) / 1000

PHASE_CHANGE_POWER = 3.8

##############################################################################
# Solcast modi
##############################################################################

SOLCAST_SAFE = "Veilig"

SOLCAST_NORMAL = "Normaal"

SOLCAST_MAX = "Maximaal zon"


##############################################################################
# Logging
##############################################################################

DEBUG = True

LOG_PREFIX = "[EV Planner]"

