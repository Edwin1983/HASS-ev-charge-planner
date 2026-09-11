from __future__ import annotations

DOMAIN = "ev_planner"

PLATFORMS = [
    "switch",
    "sensor",
    "binary_sensor",
]

# Dispatcher-signaal waarmee de controller de sensors
# vertelt dat er nieuwe data beschikbaar is.
SIGNAL_SENSOR_UPDATE = "ev_planner_sensor_update"


##############################################################################
# Config flow / options flow keys
#
# Deze keys worden gebruikt in ConfigEntry.data / ConfigEntry.options.
##############################################################################

CONF_ENTITY_DEPARTURE = "entity_departure"
CONF_ENTITY_DEPARTURE_DAY = "entity_departure_day"
CONF_ENTITY_ENERGY_NEEDED = "entity_energy_needed"
CONF_ENTITY_MAX_PRICE = "entity_max_price"
CONF_ENTITY_MIN_PV_KWH = "entity_min_pv_kwh"
CONF_ENTITY_MAX_PHASE_SWITCHES = "entity_max_phase_switches"
CONF_ENTITY_PLANNER_MODE = "entity_planner_mode"
CONF_ENTITY_SOLAR_ENABLED = "entity_solar_enabled"
CONF_ENTITY_PRICES = "entity_prices"
CONF_ENTITY_SOLCAST_TODAY = "entity_solcast_today"
CONF_ENTITY_SOLCAST_TOMORROW = "entity_solcast_tomorrow"
CONF_MAX_CHARGE_POWER_KW = "max_charge_power_kw"

# Standaardwaarden.
#
# Dit zijn de entity-ID's van het oorspronkelijke Pyscript-project.
# Ze worden alleen gebruikt om het configuratiescherm voor te vullen;
# de daadwerkelijk gebruikte waarden komen uit de ConfigEntry.
DEFAULT_ENTITY_DEPARTURE = "input_datetime.ev_vertrektijd"
DEFAULT_ENTITY_DEPARTURE_DAY = "input_select.ev_vertrekdag"
DEFAULT_ENTITY_ENERGY_NEEDED = "input_number.ev_kwh_nodig"
DEFAULT_ENTITY_MAX_PRICE = "input_number.ev_max_prijs"
DEFAULT_ENTITY_MIN_PV_KWH = "input_number.ev_min_pv_kwh"
DEFAULT_ENTITY_MAX_PHASE_SWITCHES = "input_number.ev_max_fasewisselingen"
DEFAULT_ENTITY_PLANNER_MODE = "input_select.ev_planner_mode"
DEFAULT_ENTITY_SOLAR_ENABLED = "input_boolean.alleen_zonneladen"
DEFAULT_ENTITY_PRICES = "sensor.zonneplan_current_electricity_tariff"
DEFAULT_ENTITY_SOLCAST_TODAY = "sensor.solcast_pv_forecast_forecast_today"
DEFAULT_ENTITY_SOLCAST_TOMORROW = "sensor.solcast_pv_forecast_forecast_tomorrow"
DEFAULT_MAX_CHARGE_POWER_KW = 11.04
