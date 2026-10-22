from __future__ import annotations

DOMAIN = "ev_planner"

PLATFORMS = [
    "switch",
    "sensor",
    "binary_sensor",
    "select",
    "number",
    "time",
]

SIGNAL_SENSOR_UPDATE = "ev_planner_sensor_update"


##############################################################################
# Config flow / options flow keys
##############################################################################

CONF_ENTITY_DEPARTURE = "entity_departure"
CONF_ENTITY_DEPARTURE_DAY = "entity_departure_day"
CONF_ENTITY_ENERGY_NEEDED = "entity_energy_needed"
CONF_ENTITY_MAX_PRICE = "entity_max_price"
CONF_ENTITY_MIN_PV_KWH = "entity_min_pv_kwh"
CONF_ENTITY_MAX_PHASE_SWITCHES = "entity_max_phase_switches"
CONF_ENTITY_PLANNER_MODE = "entity_planner_mode"
CONF_ENTITY_PV_ROUNDING = "entity_pv_rounding"
CONF_ENTITY_SOLAR_ENABLED = "entity_solar_enabled"
CONF_ENTITY_PRICES = "entity_prices"
CONF_ENTITY_PRICE_FORECAST = "entity_price_forecast"
CONF_ENTITY_SOLCAST_TODAY = "entity_solcast_today"
CONF_ENTITY_SOLCAST_TOMORROW = "entity_solcast_tomorrow"
CONF_MAX_CHARGE_POWER_KW = "max_charge_power_kw"

# Optionele laadregeling (charge_manager.py). Alle velden zijn optioneel;
# zonder modus- en stroom-entiteit is de laadregeling niet beschikbaar.
CONF_CHARGER_MODE_ENTITY = "charger_mode_entity"
CONF_CHARGER_CONNECTED_STATES = "charger_connected_states"
CONF_CHARGER_CHARGING_STATES = "charger_charging_states"
CONF_CHARGER_STARTABLE_STATES = "charger_startable_states"
CONF_CHARGER_CURRENT_ENTITY = "charger_current_entity"
CONF_CHARGER_START_ENTITY = "charger_start_entity"
CONF_CHARGER_STOP_ENTITY = "charger_stop_entity"
CONF_CHARGER_PHASE_ENTITY = "charger_phase_entity"
CONF_CHARGER_PHASE_ONE_VALUE = "charger_phase_one_value"
CONF_CHARGER_PHASE_THREE_VALUE = "charger_phase_three_value"
CONF_CHARGER_ENERGY_ENTITY = "charger_energy_entity"


##############################################################################
# Native entity unique IDs
##############################################################################

NATIVE_DEPARTURE = "departure"
NATIVE_DEPARTURE_DAY = "departure_day"
NATIVE_ENERGY_NEEDED = "energy_needed"
NATIVE_MAX_PRICE = "max_price"
NATIVE_MAX_PHASE_SWITCHES = "max_phase_switches"
NATIVE_MAX_PAUSES = "max_pauses"
NATIVE_PROGRESS_THRESHOLD = "progress_threshold"
NATIVE_FORECAST_MARKUP = "forecast_markup"
NATIVE_MAX_ENERGY_NEEDED = "max_energy_needed"
NATIVE_EXTRA_CHARGE_PRICE = "extra_charge_price"
DEFAULT_EXTRA_CHARGE_PRICE_CT = 5.0
DEFAULT_PROGRESS_THRESHOLD_KWH = 0.5

# Waarde van "maximaal aantal pauzes" die geen limiet betekent.
MAX_PAUSES_UNLIMITED = 10
NATIVE_MIN_PV_KWH = "min_pv_kwh"
NATIVE_MAX_CHARGE_POWER = "max_charge_power"


##############################################################################
# Planner modes
##############################################################################

PLANNER_MODE_NORMAL = "Normaal laden"
PLANNER_MODE_SOLAR_ONLY = "Alleen zonneladen"


##############################################################################
# PV laadstroom afronding
##############################################################################

PV_ROUNDING_DOWN = "Naar beneden — geen netenergie"
PV_ROUNDING_UP = "Naar boven — kleine netaanvulling toegestaan"


# Legacy standaardwaarden.
DEFAULT_ENTITY_DEPARTURE = "input_datetime.ev_vertrektijd"
DEFAULT_ENTITY_DEPARTURE_DAY = "input_select.ev_vertrekdag"
DEFAULT_ENTITY_ENERGY_NEEDED = "input_number.ev_kwh_nodig"
DEFAULT_ENTITY_MAX_PRICE = "input_number.ev_max_prijs"
DEFAULT_ENTITY_MIN_PV_KWH = "input_number.ev_min_pv_kwh"
DEFAULT_ENTITY_MAX_PHASE_SWITCHES = "input_number.ev_max_fasewisselingen"
DEFAULT_ENTITY_PLANNER_MODE = "input_select.ev_planner_mode"
DEFAULT_ENTITY_PV_ROUNDING = "input_select.ev_pv_afronding"
DEFAULT_ENTITY_SOLAR_ENABLED = "input_boolean.alleen_zonneladen"
DEFAULT_ENTITY_PRICES = "sensor.zonneplan_current_quarter_hourly_electricity_tariff"
# Opslag (EUR/kWh) op voorspelde prijzen, als marge voor onzekerheid.
FORECAST_PRICE_MARKUP = 0.02
DEFAULT_ENTITY_PRICE_FORECAST = "sensor.energy_price_forecast_eu_nl_current_retail_price"
DEFAULT_ENTITY_SOLCAST_TODAY = "sensor.solcast_pv_forecast_forecast_today"
DEFAULT_ENTITY_SOLCAST_TOMORROW = "sensor.solcast_pv_forecast_forecast_tomorrow"
DEFAULT_MAX_CHARGE_POWER_KW = 11.04

# Voorgestelde waarden voor de laadregeling (uit de oorspronkelijke
# laadregeling-automation). Alleen een voorinvulling in het opties-formulier.
DEFAULT_CHARGER_MODE_ENTITY = "sensor.ev_charger_status"
DEFAULT_CHARGER_CONNECTED_STATES = (
    "connected_requesting,connected_charging,connected_finished"
)
DEFAULT_CHARGER_CHARGING_STATES = "connected_charging"
DEFAULT_CHARGER_STARTABLE_STATES = "connected_requesting,connected_finished"
DEFAULT_CHARGER_CURRENT_ENTITY = "number.ev_charger_max_current"
DEFAULT_CHARGER_START_ENTITY = "switch.ev_charger_charging"
DEFAULT_CHARGER_STOP_ENTITY = "button.ev_charger_stop_charging"
DEFAULT_CHARGER_PHASE_ENTITY = (
    "number.ev_charger_phase_switch"
)
DEFAULT_CHARGER_ENERGY_ENTITY = "sensor.ev_charger_session_total_charge"
DEFAULT_CHARGER_PHASE_ONE_VALUE = 32
DEFAULT_CHARGER_PHASE_THREE_VALUE = 0

CHARGER_DEFAULTS = {
    CONF_CHARGER_MODE_ENTITY: DEFAULT_CHARGER_MODE_ENTITY,
    CONF_CHARGER_CONNECTED_STATES: DEFAULT_CHARGER_CONNECTED_STATES,
    CONF_CHARGER_CHARGING_STATES: DEFAULT_CHARGER_CHARGING_STATES,
    CONF_CHARGER_STARTABLE_STATES: DEFAULT_CHARGER_STARTABLE_STATES,
    CONF_CHARGER_CURRENT_ENTITY: DEFAULT_CHARGER_CURRENT_ENTITY,
    CONF_CHARGER_START_ENTITY: DEFAULT_CHARGER_START_ENTITY,
    CONF_CHARGER_STOP_ENTITY: DEFAULT_CHARGER_STOP_ENTITY,
    CONF_CHARGER_PHASE_ENTITY: DEFAULT_CHARGER_PHASE_ENTITY,
    CONF_CHARGER_ENERGY_ENTITY: DEFAULT_CHARGER_ENERGY_ENTITY,
    CONF_CHARGER_PHASE_ONE_VALUE: DEFAULT_CHARGER_PHASE_ONE_VALUE,
    CONF_CHARGER_PHASE_THREE_VALUE: DEFAULT_CHARGER_PHASE_THREE_VALUE,
}
