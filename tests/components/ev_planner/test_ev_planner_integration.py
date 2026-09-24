from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.components import ev_planner
from homeassistant.components.ev_planner.number import (
    EVPlannerEnergyNeeded,
    EVPlannerMaxChargePower,
    EVPlannerMaxPhaseSwitches,
    EVPlannerMaxPrice,
    EVPlannerMinPv,
)
from homeassistant.components.ev_planner.select import (
    DEPARTURE_DAYS,
    PLANNER_MODES,
    PV_ROUNDING_OPTIONS,
    EVPlannerDepartureDaySelect,
    EVPlannerModeSelect,
    EVPlannerPvRoundingSelect,
)
from homeassistant.components.ev_planner.sensor import _current_decision
from homeassistant.components.ev_planner.time import EVPlannerDepartureTime
from homeassistant.components.ev_planner.core.homeassistant import (
    HomeAssistant as PlannerHomeAssistant,
)
from homeassistant.components.ev_planner.core.logger import Logger
from homeassistant.components.ev_planner.const import (
    PV_ROUNDING_DOWN,
    PV_ROUNDING_UP,
)
from homeassistant.components.ev_planner.core.models import Hour
from homeassistant.components.ev_planner.core.planner import (
    ChargingDecision,
    ChargingPlan,
)
from homeassistant.components.ev_planner.core.scheduler import (
    EVScheduler,
    SchedulerSettings,
)
from homeassistant.components.ev_planner.core.solar import (
    _option,
    apply_solar_only,
)
from homeassistant.components.ev_planner.core.status import (
    EVStatus,
    EVStatusManager,
)
from homeassistant.components.ev_planner.core.solcast import SolcastReader
from homeassistant.components.ev_planner.core.utils import (
    clamp,
    hour_key,
    overlap,
    parse_datetime,
)

from custom_components.ev_planner.const import (
    CONF_ENTITY_DEPARTURE,
    CONF_ENTITY_DEPARTURE_DAY,
    CONF_ENTITY_ENERGY_NEEDED,
    CONF_ENTITY_MAX_PHASE_SWITCHES,
    CONF_ENTITY_MAX_PRICE,
    CONF_ENTITY_MIN_PV_KWH,
    CONF_ENTITY_PLANNER_MODE,
    CONF_ENTITY_PV_ROUNDING,
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    CONF_MAX_CHARGE_POWER_KW,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def ignore_missing_translations(request: pytest.FixtureRequest) -> list[str]:
    """Ignore unrelated Home Assistant Core translation checks per test."""
    ignores = {
        "test_full_integration_setup_and_unload": [
            "component.select.services.select_last.name",
        ],
        "test_native_only_configuration": [
            "component.switch.services.turn_on.name",
        ],
        "test_full_planning_chain": [
            "component.switch.services.toggle.name",
        ],
        "test_service_rejects_unknown_config_entry": [
            "component.number.services.set_value.name",
        ],
        "test_service_rejects_unloaded_config_entry": [
            "component.switch.services.turn_on.name",
        ],
    }
    return ignores.get(request.node.name, [])


def make_config():
    return {
        CONF_ENTITY_DEPARTURE: "input_datetime.ev_vertrektijd",
        CONF_ENTITY_DEPARTURE_DAY: "input_select.ev_vertrekdag",
        CONF_ENTITY_ENERGY_NEEDED: "input_number.ev_kwh_nodig",
        CONF_ENTITY_MAX_PRICE: "input_number.ev_max_prijs",
        CONF_ENTITY_MIN_PV_KWH: "input_number.ev_min_pv_kwh",
        CONF_ENTITY_MAX_PHASE_SWITCHES: "input_number.ev_max_fasewisselingen",
        CONF_ENTITY_PV_ROUNDING: "input_select.ev_pv_afronding",
        CONF_ENTITY_PRICES: "sensor.zonneplan_current_electricity_tariff",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
        CONF_MAX_CHARGE_POWER_KW: 11.04,
    }


async def test_current_decision_ignores_invalid_timestamps() -> None:
    """Invalid decision timestamps are ignored."""
    data = {
        "attributes": {
            "decisions": [
                {"start": "not-a-timestamp", "end": "also-invalid"},
            ]
        }
    }

    assert _current_decision(data) is None


async def test_time_parse_and_setter_paths(hass: HomeAssistant) -> None:
    """Cover time parsing fallbacks and the native value setter."""
    assert EVPlannerDepartureTime._parse_state(None) is None
    assert EVPlannerDepartureTime._parse_state("12:34:56") == dt.time(12, 34, 56)
    assert EVPlannerDepartureTime._parse_state(
        "2026-09-22T12:34:56"
    ) == dt.time(12, 34, 56)
    assert EVPlannerDepartureTime._parse_state("not-a-time") is None

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Planner",
        data=make_config(),
        unique_id="time-setter",
    )
    entry.add_to_hass(hass)
    entity = EVPlannerDepartureTime(hass, entry)

    with patch.object(entity, "async_write_ha_state") as write_state:
        await entity.async_set_value(dt.time(7, 45))

    assert entity.native_value == dt.time(7, 45)
    write_state.assert_called_once()


async def test_homeassistant_wrapper_paths(hass: HomeAssistant) -> None:
    """Cover Home Assistant state, attribute, and error handling paths."""
    errors = []
    warnings = []
    logger = SimpleNamespace(error=errors.append, warning=warnings.append)
    wrapper = PlannerHomeAssistant(hass, logger)

    hass.states.async_set("sensor.test", "active", {"value": 42})
    assert wrapper.get_state("sensor.test") == "active"
    assert wrapper.get_state("sensor.test", "value") == 42
    assert wrapper.get_state("sensor.missing") is None
    assert wrapper.get_attributes("sensor.test") == {"value": 42}
    assert wrapper.get_attributes("sensor.missing") == {}
    assert wrapper.get_state_object("sensor.test") is not None

    hass.states.async_set("sensor.bad_attributes", "active", None)
    state = hass.states.get("sensor.bad_attributes")
    assert state is not None
    state.attributes = "invalid"
    assert wrapper.get_state("sensor.bad_attributes", "value") is None
    assert wrapper.get_attributes("sensor.bad_attributes") == {}

    error_states = SimpleNamespace(
        get=lambda entity_id: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    error_hass = SimpleNamespace(states=error_states)
    error_wrapper = PlannerHomeAssistant(error_hass, logger)

    assert error_wrapper.get_state("sensor.test") is None
    assert error_wrapper.get_attributes("sensor.test") == {}
    assert error_wrapper.get_state_object("sensor.test") is None

    assert errors
    assert warnings


async def test_logger_paths() -> None:
    """Cover logger methods with and without configured callbacks."""
    messages = []
    logger = Logger(
        debug=messages.append,
        info=messages.append,
        warning=messages.append,
        error=messages.append,
    )

    logger.debug("debug")
    logger.info("info")
    logger.warning("warning")
    logger.error("error")

    assert messages == [
        "[EV Planner] debug",
        "[EV Planner] info",
        "[EV Planner] WARNING: warning",
        "[EV Planner] ERROR: error",
    ]

    Logger().debug("ignored")
    Logger().info("ignored")
    Logger().warning("ignored")
    Logger().error("ignored")


async def test_number_restore_and_fallback_paths(hass: HomeAssistant) -> None:
    """Cover native restore, legacy fallback, and default paths for numbers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Planner",
        data=make_config(),
        unique_id="number-fallbacks",
    )
    entry.add_to_hass(hass)

    hass.states.async_set("input_number.ev_kwh_nodig", "7.5")
    energy = EVPlannerEnergyNeeded(hass, entry)
    with patch.object(
        energy,
        "async_get_last_state",
        return_value=SimpleNamespace(state="not-a-number"),
    ):
        await energy._restore_or_legacy(10.0, CONF_ENTITY_ENERGY_NEEDED)
    assert energy.native_value == 7.5

    hass.states.async_set(
        "input_number.ev_max_fasewisselingen", "not-a-number"
    )
    phase_switches = EVPlannerMaxPhaseSwitches(hass, entry)
    with patch.object(
        phase_switches,
        "async_get_last_state",
        return_value=SimpleNamespace(state="invalid"),
    ):
        await phase_switches._restore_or_legacy(8.0, CONF_ENTITY_MAX_PHASE_SWITCHES)
    assert phase_switches.native_value == 8.0

    with patch.object(phase_switches, "async_write_ha_state") as write_state:
        await phase_switches.async_set_native_value(9.0)
    assert phase_switches.native_value == 9.0
    write_state.assert_called_once()

    hass.states.async_set("input_number.ev_max_prijs", "0.25")
    max_price = EVPlannerMaxPrice(hass, entry)
    with patch.object(
        max_price,
        "async_get_last_state",
        return_value=SimpleNamespace(state="invalid", attributes={}),
    ):
        await max_price.async_added_to_hass()
    assert max_price.native_value == 25.0

    max_price = EVPlannerMaxPrice(hass, entry)
    with patch.object(
        max_price,
        "async_get_last_state",
        return_value=SimpleNamespace(
            state="0.25", attributes={"unit_of_measurement": "€/kWh"}
        ),
    ):
        await max_price.async_added_to_hass()
    assert max_price.native_value == 25.0

    hass.states.async_set("input_number.ev_max_prijs", "invalid")
    max_price = EVPlannerMaxPrice(hass, entry)
    with patch.object(
        max_price,
        "async_get_last_state",
        return_value=SimpleNamespace(state="invalid", attributes={}),
    ):
        await max_price.async_added_to_hass()
    assert max_price.native_value == 0.0

    max_power = EVPlannerMaxChargePower(hass, entry)
    with patch.object(
        max_power,
        "async_get_last_state",
        return_value=SimpleNamespace(state="invalid"),
    ):
        await max_power.async_added_to_hass()
    assert max_power.native_value == 11.04

    hass.config_entries.async_update_entry(
        entry, options={CONF_MAX_CHARGE_POWER_KW: "invalid"}
    )
    await hass.async_block_till_done()
    max_power = EVPlannerMaxChargePower(hass, entry)
    with patch.object(
        max_power,
        "async_get_last_state",
        return_value=None,
    ):
        await max_power.async_added_to_hass()
    assert max_power.native_value == 11.04

    min_pv = EVPlannerMinPv(hass, entry)
    with patch.object(min_pv, "async_get_last_state", return_value=None):
        await min_pv._restore_or_legacy(0.0, CONF_ENTITY_MIN_PV_KWH)
    assert min_pv.native_value == 0.0


async def test_select_fallback_and_setter_paths(hass: HomeAssistant) -> None:
    """Cover select legacy fallbacks and option validation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Planner",
        data={
            CONF_ENTITY_DEPARTURE_DAY: "input_select.ev_vertrekdag",
            CONF_ENTITY_PLANNER_MODE: "input_select.ev_planner_mode",
            CONF_ENTITY_PV_ROUNDING: "input_select.ev_pv_afronding",
        },
        unique_id="select-fallbacks",
    )
    entry.add_to_hass(hass)

    hass.states.async_set("input_select.ev_vertrekdag", DEPARTURE_DAYS[1])
    departure = EVPlannerDepartureDaySelect(hass, entry)
    with patch.object(departure, "async_get_last_state", return_value=None):
        await departure.async_added_to_hass()
    assert departure.current_option == DEPARTURE_DAYS[1]
    await departure.async_select_option("invalid")
    with patch.object(departure, "async_write_ha_state") as write_state:
        await departure.async_select_option(DEPARTURE_DAYS[1])
    assert departure.current_option == DEPARTURE_DAYS[1]
    write_state.assert_called_once()

    hass.states.async_set("input_select.ev_planner_mode", PLANNER_MODES[1])
    mode = EVPlannerModeSelect(hass, entry)
    with patch.object(mode, "async_get_last_state", return_value=None):
        await mode.async_added_to_hass()
    assert mode.current_option == PLANNER_MODES[1]
    await mode.async_select_option("invalid")
    with patch.object(mode, "async_write_ha_state") as write_state:
        await mode.async_select_option(PLANNER_MODES[1])
    assert mode.current_option == PLANNER_MODES[1]
    write_state.assert_called_once()

    hass.states.async_set("input_select.ev_pv_afronding", PV_ROUNDING_OPTIONS[1])
    rounding = EVPlannerPvRoundingSelect(hass, entry)
    with patch.object(rounding, "async_get_last_state", return_value=None):
        await rounding.async_added_to_hass()
    assert rounding.current_option == PV_ROUNDING_OPTIONS[1]
    await rounding.async_select_option("invalid")
    with patch.object(rounding, "async_write_ha_state") as write_state:
        await rounding.async_select_option(PV_ROUNDING_OPTIONS[1])
    assert rounding.current_option == PV_ROUNDING_OPTIONS[1]
    write_state.assert_called_once()


async def test_full_integration_setup_and_unload(
    hass: HomeAssistant, enable_custom_integrations: None
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Planner",
        data=make_config(),
        unique_id="test-entry",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not None
    assert hass.services.has_service(DOMAIN, "update")
    assert hass.services.has_service(DOMAIN, "create_plan")
    assert hass.services.has_service(DOMAIN, "replan")
    assert hass.services.has_service(DOMAIN, "clear_plan")
    assert hass.services.has_service(DOMAIN, "status")
    assert hass.services.has_service(DOMAIN, "dashboard")

    status_response = await hass.services.async_call(
        DOMAIN, "status", {"config_entry_id": entry.entry_id},
        blocking=True, return_response=True,
    )
    assert isinstance(status_response, dict)
    dashboard_response = await hass.services.async_call(
        DOMAIN, "dashboard", {"config_entry_id": entry.entry_id},
        blocking=True, return_response=True,
    )
    assert isinstance(dashboard_response, dict)

    states = hass.states
    registry = er.async_get(hass)

    sensor_state = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_state"
    )
    sensor_data = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_data"
    )
    sensor_charge_current = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_desired_charge_current"
    )
    sensor_phases = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_desired_phases"
    )

    assert sensor_state is not None
    assert sensor_data is not None
    assert sensor_charge_current is not None
    assert sensor_phases is not None

    for entity_id in (
        sensor_state, sensor_data,
        sensor_charge_current, sensor_phases,
        "binary_sensor.ev_planner_charging_allowed",
        "switch.ev_planner_smart_charging",
        "select.ev_charge_planner_departure_day",
        "select.ev_charge_planner_planner_mode",
        "select.ev_charge_planner_pv_charging_current_rounding",
        "time.ev_charge_planner_departure_time",
        "number.ev_charge_planner_energy_needed",
        "number.ev_charge_planner_maximum_grid_price",
        "number.ev_charge_planner_maximum_phase_switches",
        "number.ev_charge_planner_minimum_pv_for_solar_only",
        "number.ev_charge_planner_maximum_charging_power",
    ):
        assert states.get(entity_id) is not None

    energy_needed = states.get("number.ev_charge_planner_energy_needed")
    max_grid_price = states.get("number.ev_charge_planner_maximum_grid_price")
    min_pv = states.get("number.ev_charge_planner_minimum_pv_for_solar_only")
    assert energy_needed.attributes["mode"] == "slider"
    assert max_grid_price.attributes["mode"] == "slider"
    assert min_pv.attributes["mode"] == "slider"
    assert max_grid_price.attributes["unit_of_measurement"] == "ct/kWh"
    assert max_grid_price.attributes["step"] == 1.0

    config_entities = (
        "switch.ev_planner_smart_charging",
        "select.ev_charge_planner_departure_day",
        "select.ev_charge_planner_planner_mode",
        "select.ev_charge_planner_pv_charging_current_rounding",
        "time.ev_charge_planner_departure_time",
        "number.ev_charge_planner_energy_needed",
        "number.ev_charge_planner_maximum_grid_price",
        "number.ev_charge_planner_maximum_phase_switches",
        "number.ev_charge_planner_minimum_pv_for_solar_only",
        "number.ev_charge_planner_maximum_charging_power",
    )
    for entity_id in config_entities:
        registry_entry = registry.async_get(entity_id)
        assert registry_entry is not None
        assert registry_entry.entity_category is EntityCategory.CONFIG
        assert registry_entry.unique_id.startswith(f"{entry.entry_id}_")

    charging_allowed = registry.async_get("binary_sensor.ev_planner_charging_allowed")
    assert charging_allowed is not None
    assert charging_allowed.unique_id == f"{entry.entry_id}_charging_allowed"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.ev_planner_smart_charging") is None
    assert hass.states.get(sensor_state) is None
    assert hass.services.has_service(DOMAIN, "update")


async def test_native_only_configuration(
    hass: HomeAssistant, enable_custom_integrations: None
):
    """The integration can be set up without legacy input helpers."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Planner",
        data={
            CONF_ENTITY_PRICES: "sensor.zonneplan_current_electricity_tariff",
            CONF_ENTITY_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
            CONF_ENTITY_SOLCAST_TOMORROW: (
                "sensor.solcast_pv_forecast_forecast_tomorrow"
            ),
        },
        unique_id="native-only",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("time.ev_charge_planner_departure_time") is not None
    assert hass.states.get("select.ev_charge_planner_departure_day") is not None
    assert hass.states.get("number.ev_charge_planner_energy_needed") is not None
    assert hass.states.get("number.ev_charge_planner_maximum_grid_price") is not None
    assert hass.states.get(
        "number.ev_charge_planner_maximum_phase_switches"
    ) is not None
    assert hass.states.get(
        "number.ev_charge_planner_minimum_pv_for_solar_only"
    ) is not None
    assert hass.states.get(
        "number.ev_charge_planner_maximum_charging_power"
    ) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_full_planning_chain(
    hass: HomeAssistant, enable_custom_integrations: None
):
    """Exercise config -> readers -> planner -> scheduler -> native output."""
    now = dt.datetime.now().astimezone()
    base = now.replace(minute=0, second=0, microsecond=0)
    departure = base + dt.timedelta(hours=4)

    hass.states.async_set(
        "input_datetime.ev_vertrektijd", departure.strftime("%H:%M:%S")
    )
    hass.states.async_set(
        "input_select.ev_vertrekdag",
        "Vandaag" if departure.date() == base.date() else "Morgen",
    )
    hass.states.async_set("input_number.ev_kwh_nodig", "2.0")
    hass.states.async_set("input_number.ev_max_prijs", "0.20")
    hass.states.async_set("input_number.ev_min_pv_kwh", "0.0")
    hass.states.async_set("input_number.ev_max_fasewisselingen", "8")
    hass.states.async_set(
        "input_select.ev_pv_afronding", "Naar beneden — geen netenergie"
    )

    forecast = []
    solcast_today = []
    solcast_tomorrow = []
    for index in range(6):
        start = base + dt.timedelta(hours=index)
        forecast.append(
            {"start_date": start.isoformat(), "electricity_price": 1_000_000}
        )
        item = {
            "period_start": start.isoformat(),
            "pv_estimate": 0.0,
            "pv_estimate10": 0.0,
            "pv_estimate90": 0.0,
        }
        target = solcast_today if start.date() == base.date() else solcast_tomorrow
        target.append(item)

    hass.states.async_set(
        "sensor.zonneplan_current_electricity_tariff", "0.10", {"forecast": forecast}
    )
    hass.states.async_set(
        "sensor.solcast_pv_forecast_forecast_today", "0",
        {"detailedHourly": solcast_today},
    )
    hass.states.async_set(
        "sensor.solcast_pv_forecast_forecast_tomorrow", "0",
        {"detailedHourly": solcast_tomorrow},
    )

    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Planner", data=make_config(),
        unique_id="planning-chain",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.ev_planner_smart_charging"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.ev_planner_smart_charging").state == "on"

    await hass.services.async_call(
        DOMAIN, "update", {"config_entry_id": entry.entry_id}, blocking=True,
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    sensor_data = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_data"
    )
    sensor_charge_current = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_desired_charge_current"
    )
    sensor_phases = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_desired_phases"
    )
    sensor_state = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_state"
    )

    assert sensor_data is not None
    assert sensor_charge_current is not None
    assert sensor_phases is not None
    assert sensor_state is not None

    data_state = hass.states.get(sensor_data)
    assert data_state is not None
    assert data_state.attributes["decisions"]
    assert data_state.attributes["energy_planned_kwh"] > 0
    charge_current = hass.states.get(sensor_charge_current)
    phases = hass.states.get(sensor_phases)
    assert charge_current is not None
    assert phases is not None
    assert 0 <= int(charge_current.state) <= 16
    assert int(phases.state) in {0, 1, 3}
    state = hass.states.get(sensor_state)
    assert state is not None
    assert state.state not in {
        "Geen prijsdata", "Geen PV-data",
        "Fout bij plannen", "Ongeldige plannerinstellingen",
    }

    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.ev_planner_smart_charging"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.ev_planner_smart_charging").state == "off"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_service_rejects_unknown_config_entry(hass: HomeAssistant):
    """Service actions reject an unknown config entry."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "status", {"config_entry_id": "does-not-exist"},
            blocking=True, return_response=True,
        )


async def test_service_rejects_unloaded_config_entry(
    hass: HomeAssistant, enable_custom_integrations: None
):
    """Service actions reject a config entry that is not loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Planner", data=make_config(),
        unique_id="unloaded-entry",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "status", {"config_entry_id": entry.entry_id},
            blocking=True, return_response=True,
        )


async def test_service_actions_and_update_listener(
    hass: HomeAssistant, enable_custom_integrations: None
):
    """Exercise all non-response service handlers and the options listener."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Planner", data=make_config(),
        unique_id="service-actions",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with (
        patch.object(entry.runtime_data, "update") as update,
        patch.object(entry.runtime_data, "create_plan") as create_plan,
        patch.object(entry.runtime_data, "replan") as replan,
        patch.object(entry.runtime_data, "clear_plan") as clear_plan,
    ):
        await hass.services.async_call(
            DOMAIN, "update", {"config_entry_id": entry.entry_id}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN, "create_plan", {"config_entry_id": entry.entry_id}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN, "replan", {"config_entry_id": entry.entry_id}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN, "clear_plan", {"config_entry_id": entry.entry_id}, blocking=True
        )

    update.assert_called_once()
    create_plan.assert_called_once()
    replan.assert_called_once()
    clear_plan.assert_called_once()

    hass.config_entries.async_update_entry(
        entry, options={"update_interval_minutes": 2}
    )
    await hass.async_block_till_done()


async def test_service_rejects_wrong_config_entry_domain(
    hass: HomeAssistant, enable_custom_integrations: None
):
    """Service actions reject a config entry from another domain."""
    call = SimpleNamespace(
        hass=hass,
        data={"config_entry_id": "wrong-domain"},
    )
    with patch.object(
        hass.config_entries,
        "async_get_entry",
        return_value=SimpleNamespace(domain="other_domain"),
    ):
        with pytest.raises(ServiceValidationError):
            await ev_planner._async_handle_status(call)


async def test_core_utils_paths() -> None:
    """Cover the small pure utility functions."""
    value = dt.datetime(2026, 9, 22, 13, 42, 17, 123456)
    assert parse_datetime(value.isoformat()) == value
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10
    assert hour_key(value) == dt.datetime(2026, 9, 22, 13, 0, 0, tzinfo=value.tzinfo)
    assert overlap(
        dt.datetime(2026, 9, 22, 10),
        dt.datetime(2026, 9, 22, 11),
        dt.datetime(2026, 9, 22, 10, 30),
        dt.datetime(2026, 9, 22, 12),
    )
    assert not overlap(
        dt.datetime(2026, 9, 22, 10),
        dt.datetime(2026, 9, 22, 11),
        dt.datetime(2026, 9, 22, 11),
        dt.datetime(2026, 9, 22, 12),
    )


def _solar_planner(pv_rounding: str = PV_ROUNDING_DOWN):
    """Build a minimal planner double for solar-only tests."""
    settings = SimpleNamespace(
        pv_rounding=pv_rounding,
        max_phase_switches=8,
        min_pv_kwh=0.0,
        energy_needed_kwh=99.0,
    )
    planner = SimpleNamespace(settings=settings)
    planner._hour_duration = lambda hour: (
        hour.end - hour.start
    ).total_seconds() / 3600.0
    planner._valid_currents = lambda phases: list(range(6, 17))
    planner._actual_power_for_current = (
        lambda current_a, phases: 0.23 * current_a * phases
    )
    return planner


async def test_core_solar_option_paths() -> None:
    """Cover solar option rounding, rejection, and invalid configuration."""
    planner = _solar_planner()
    start = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)

    hour = Hour(start=start, end=start + dt.timedelta(hours=1), pv_estimate=4.0)
    option = _option(planner, hour, 1)
    assert option is not None
    assert option[0] == 16
    assert option[1] == 1
    assert option[3] == 3.68
    assert option[4] == pytest.approx(0.0)

    assert _option(
        planner,
        Hour(start=start, end=start, pv_estimate=4.0),
        1,
    ) is None
    assert _option(
        planner,
        Hour(start=start, end=start + dt.timedelta(hours=1), pv_estimate=0.0),
        1,
    ) is None

    planner.settings.pv_rounding = PV_ROUNDING_UP
    option = _option(
        planner,
        Hour(start=start, end=start + dt.timedelta(hours=1), pv_estimate=1.0),
        1,
    )
    assert option is not None
    assert option[0] == 6
    assert option[4] > 0

    planner.settings.pv_rounding = "invalid"
    with pytest.raises(ValueError, match="Ongeldige pv_rounding"):
        _option(
            planner,
            Hour(start=start, end=start + dt.timedelta(hours=1), pv_estimate=2.0),
            1,
        )

    planner._valid_currents = lambda phases: []
    assert _option(
        planner,
        Hour(start=start, end=start + dt.timedelta(hours=1), pv_estimate=2.0),
        1,
    ) is None


async def test_core_solar_apply_paths() -> None:
    """Cover solar-only planning, skips, phase choices, and switch limits."""
    planner = _solar_planner(PV_ROUNDING_DOWN)
    start = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)

    hours = [
        Hour(
            start=start,
            end=start + dt.timedelta(hours=1),
            pv_estimate=3.0,
        ),
        Hour(
            start=start + dt.timedelta(hours=1),
            end=start + dt.timedelta(hours=2),
            pv_estimate=0.0,
        ),
    ]
    apply_solar_only(planner, hours)
    assert hours[0].selected
    assert hours[0].reason == "Alleen zonneladen"
    assert not hours[1].selected
    assert planner.settings.energy_needed_kwh > 0

    planner.settings.min_pv_kwh = 4.0
    apply_solar_only(planner, hours)
    assert not hours[0].selected

    planner.settings.min_pv_kwh = 0.0
    planner.settings.max_phase_switches = 0
    hours = [
        Hour(
            start=start,
            end=start + dt.timedelta(hours=1),
            pv_estimate=2.0,
        ),
        Hour(
            start=start + dt.timedelta(hours=1),
            end=start + dt.timedelta(hours=2),
            pv_estimate=5.0,
        ),
    ]
    apply_solar_only(planner, hours)
    assert all(hour.selected for hour in hours)


async def test_core_scheduler_paths() -> None:
    """Cover scheduler setup, runtime transitions, selection and clearing."""
    logger = Logger()
    with pytest.raises(ValueError):
        SchedulerSettings(-1)
    with pytest.raises(TypeError):
        EVScheduler(object(), logger)

    settings = SchedulerSettings(min_charge_energy=0.5)
    scheduler = EVScheduler(settings, logger)
    assert not scheduler.has_plan()
    assert scheduler.selected_hours() == []

    start = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    hour = Hour(
        start=start,
        end=start + dt.timedelta(hours=1),
        charge_energy=1.0,
        free_energy=0.2,
        paid_energy=0.8,
        price=0.20,
        charge_power_w=3680.0,
        charge_current_a=16.0,
        phases=1,
        selected=True,
        reason="test",
    )
    decision = ChargingDecision(
        hour=hour,
        energy_kwh=1.0,
        free_energy_kwh=0.2,
        paid_energy_kwh=0.8,
        price=0.20,
        cost=0.16,
        selected=True,
        reason="test",
        charge_power_kw=3.68,
        charge_current_a=16,
        phases=1,
    )
    plan = ChargingPlan(
        decisions=[decision],
        energy_needed_kwh=1.0,
        energy_planned_kwh=1.0,
        missing_energy_kwh=0.0,
        free_energy_kwh=0.2,
        paid_energy_kwh=0.8,
        estimated_cost=0.16,
        complete=True,
        departure_time=start + dt.timedelta(hours=2),
        max_price=0.20,
    )

    with pytest.raises(TypeError):
        scheduler.set_plan(object())
    scheduler.set_plan(plan)
    assert scheduler.has_plan()
    assert scheduler.get_current_hour(start - dt.timedelta(minutes=1)) is None
    assert scheduler.get_current_hour(start) is hour
    assert scheduler.charging_allowed(start)
    assert scheduler.next_selected_hour(start - dt.timedelta(hours=1)) is hour
    assert scheduler.next_selected_hour(start + dt.timedelta(hours=1)) is None
    assert scheduler.update(start)
    assert scheduler.current_hour() is hour
    assert scheduler.status.charging_allowed
    assert scheduler.status.charge_power_w == 3680.0
    assert scheduler.status.charge_current_a == 16.0
    assert scheduler.status.phases == 1
    assert not scheduler.update(start + dt.timedelta(minutes=10))
    assert scheduler.update(start + dt.timedelta(hours=1))
    assert scheduler.current_hour() is None
    assert not scheduler.status.charging_allowed

    scheduler.clear_plan()
    assert not scheduler.has_plan()
    assert scheduler.current_hour() is None
    assert scheduler.selected_hours() == []
    assert not scheduler.update(start)


async def test_core_status_paths() -> None:
    """Cover status serialization, manager update, and logging branches."""
    start = dt.datetime(2026, 9, 22, 10, tzinfo=dt.timezone.utc)
    hour = Hour(
        start=start,
        end=start + dt.timedelta(hours=1),
        charge_energy=2.0,
        free_energy=1.0,
        paid_energy=1.0,
        price=0.25,
        charge_power_w=5520.0,
        charge_current_a=8.0,
        phases=3,
        selected=True,
        reason="PV",
    )

    idle = EVStatus()
    idle_data = idle.as_dict()
    assert idle_data["state"] == "idle"
    assert idle_data["state_text"] == "Wachten"
    assert idle_data["start"] is None

    waiting = EVStatus(active=True, charging_allowed=False)
    assert waiting.as_dict()["state"] == "waiting"
    charging = EVStatus(active=True, charging_allowed=True)
    assert charging.as_dict()["state"] == "charging"

    scheduler = SimpleNamespace(
        current_hour=lambda: None,
        charging_allowed=lambda now: False,
    )
    manager = EVStatusManager(scheduler, Logger())
    assert manager.update(start).active is False
    assert manager.get_status().active is False
    assert manager.as_dict()["state"] == "idle"
    manager.log_status()

    scheduler.current_hour = lambda: hour
    scheduler.charging_allowed = lambda now: True
    status = manager.update(start)
    assert status.active
    assert status.charging_allowed
    assert status.start == start
    assert status.end == start + dt.timedelta(hours=1)
    assert status.charge_power_w == 5520.0
    assert status.charge_current_a == 8.0
    assert status.phases == 3
    assert status.reason == "PV"
    manager.log_status()
    assert manager.as_dict()["state"] == "charging"


async def test_core_solcast_paths() -> None:
    """Cover Solcast parsing, validation, statistics, and reader branches."""
    logger = Logger()
    now = dt.datetime.now().astimezone()
    base = now.replace(minute=0, second=0, microsecond=0)

    valid = {
        "period_start": base.isoformat(),
        "pv_estimate": 1.5,
        "pv_estimate10": -0.2,
        "pv_estimate90": 2.5,
    }
    tomorrow = {
        "period_start": (base + dt.timedelta(days=1)).isoformat(),
        "pv_estimate": 2.0,
        "pv_estimate10": 1.0,
        "pv_estimate90": 3.0,
    }

    app = SimpleNamespace(
        get_attributes=lambda entity_id: {
            "today": {"detailedHourly": [valid]},
            "tomorrow": {"detailedHourly": [tomorrow]},
        }[entity_id]
    )
    reader = SolcastReader(app, logger, "today", "tomorrow")

    data = reader.read()
    assert len(data.hours) == 2
    assert data.hours[0].pv_estimate10 == 0.0
    assert data.total_today == pytest.approx(1.5)
    assert data.total_tomorrow == pytest.approx(2.0)
    assert data.valid_until == data.hours[-1].end
    reader.dump(data)

    with pytest.raises(RuntimeError, match="vandaag ontbreekt"):
        SolcastReader(
            SimpleNamespace(get_attributes=lambda entity_id: {}),
            logger,
            "today",
            "tomorrow",
        )._read_today()

    with pytest.raises(RuntimeError, match="vandaag is geen lijst"):
        SolcastReader(
            SimpleNamespace(
                get_attributes=lambda entity_id: {"detailedHourly": "invalid"}
            ),
            logger,
            "today",
            "tomorrow",
        )._read_today()

    assert SolcastReader(
        SimpleNamespace(get_attributes=lambda entity_id: {}),
        logger,
        "today",
        "tomorrow",
    )._read_tomorrow() == []

    assert SolcastReader(
        SimpleNamespace(
            get_attributes=lambda entity_id: {"detailedHourly": "invalid"}
        ),
        logger,
        "today",
        "tomorrow",
    )._read_tomorrow() == []

    assert reader._parse_forecast([valid, {"period_start": "invalid"}])
    with pytest.raises(ValueError, match="Ontbrekende period_start"):
        reader._create_hour({})

    with pytest.raises(ValueError, match="Ongeldige period_start"):
        reader._create_hour({"period_start": "not-a-date"})

    with pytest.raises(ValueError, match="niet timezone-aware"):
        reader._create_hour(
            {"period_start": dt.datetime(2026, 9, 22, 10)}
        )

    with pytest.raises(ValueError, match="Ongeldige period_start"):
        reader._create_hour({"period_start": 123})

    with pytest.raises(ValueError, match="Ongeldige Solcast PV waarde"):
        reader._create_hour(
            {
                "period_start": base.isoformat(),
                "pv_estimate": "bad",
            }
        )

    with pytest.raises(TypeError, match="geen dictionary"):
        reader._create_hour([])

    duplicate = reader._create_hour(valid)
    unique = reader._create_hour(tomorrow)
    assert len(reader._remove_duplicates([duplicate, duplicate, unique])) == 2

    old = reader._create_hour(
        {
            "period_start": (base - dt.timedelta(hours=3)).isoformat(),
            "pv_estimate": 1,
        }
    )
    assert old not in reader._remove_past([old, duplicate])

    gap_start = base + dt.timedelta(hours=3)
    gap_with_pv = reader._create_hour(
        {
            "period_start": gap_start.isoformat(),
            "pv_estimate": 1,
        }
    )
    reader._validate_series([duplicate, gap_with_pv])

    huge_gap = reader._create_hour(
        {
            "period_start": (base + dt.timedelta(hours=6)).isoformat(),
            "pv_estimate": 1,
        }
    )
    reader._validate_series([duplicate, huge_gap])

    empty = reader._calculate_statistics([])
    assert empty.hours == []

    sorted_hours = [unique, duplicate]
    result = reader._sort(sorted_hours)
    assert result[0].start <= result[1].start
    assert [hour.hour_index for hour in result] == [0, 1]
