from __future__ import annotations

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_planner.const import (
    CONF_ENTITY_DEPARTURE,
    CONF_ENTITY_DEPARTURE_DAY,
    CONF_ENTITY_ENERGY_NEEDED,
    CONF_ENTITY_MAX_PHASE_SWITCHES,
    CONF_ENTITY_MAX_PRICE,
    CONF_ENTITY_MIN_PV_KWH,
    CONF_ENTITY_PLANNER_MODE,
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLAR_ENABLED,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    CONF_MAX_CHARGE_POWER_KW,
    DOMAIN,
)


def make_config():
    return {
        CONF_ENTITY_DEPARTURE: "input_datetime.ev_vertrektijd",
        CONF_ENTITY_DEPARTURE_DAY: "input_select.ev_vertrekdag",
        CONF_ENTITY_ENERGY_NEEDED: "input_number.ev_kwh_nodig",
        CONF_ENTITY_MAX_PRICE: "input_number.ev_max_prijs",
        CONF_ENTITY_MIN_PV_KWH: "input_number.ev_min_pv_kwh",
        CONF_ENTITY_MAX_PHASE_SWITCHES: "input_number.ev_max_fasewisselingen",
        CONF_ENTITY_PLANNER_MODE: "input_select.ev_planner_mode",
        CONF_ENTITY_SOLAR_ENABLED: "input_boolean.alleen_zonneladen",
        CONF_ENTITY_PRICES: "sensor.zonneplan_current_electricity_tariff",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
        CONF_MAX_CHARGE_POWER_KW: 11.04,
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_full_integration_setup_and_unload(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Planner",
        data=make_config(),
        unique_id="test-entry",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id]["controller"] is not None

    assert hass.services.has_service(DOMAIN, "update")
    assert hass.services.has_service(DOMAIN, "create_plan")
    assert hass.services.has_service(DOMAIN, "replan")
    assert hass.services.has_service(DOMAIN, "clear_plan")
    assert hass.services.has_service(DOMAIN, "status")
    assert hass.services.has_service(DOMAIN, "dashboard")

    status_response = await hass.services.async_call(
        DOMAIN,
        "status",
        {},
        blocking=True,
        return_response=True,
    )
    assert isinstance(status_response, dict)

    dashboard_response = await hass.services.async_call(
        DOMAIN,
        "dashboard",
        {},
        blocking=True,
        return_response=True,
    )
    assert isinstance(dashboard_response, dict)

    states = hass.states
    assert states.get("sensor.ev_planner_state") is not None
    assert states.get("sensor.ev_planner_data") is not None
    assert states.get("binary_sensor.ev_planner_charging_allowed") is not None
    assert states.get("switch.ev_planner_smart_charging") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert not hass.services.has_service(DOMAIN, "update")
