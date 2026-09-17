from __future__ import annotations

import pytest

from homeassistant.config_entries import SOURCE_USER
from pytest_homeassistant_custom_component.common import MockConfigEntry

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


def config_data():
    return {
        CONF_ENTITY_DEPARTURE: "input_datetime.ev_vertrektijd",
        CONF_ENTITY_DEPARTURE_DAY: "input_select.ev_vertrekdag",
        CONF_ENTITY_ENERGY_NEEDED: "input_number.ev_kwh_nodig",
        CONF_ENTITY_MAX_PRICE: "input_number.ev_max_prijs",
        CONF_ENTITY_MIN_PV_KWH: "input_number.ev_min_pv_kwh",
        CONF_ENTITY_MAX_PHASE_SWITCHES: "input_number.ev_max_fasewisselingen",
        CONF_ENTITY_PLANNER_MODE: "input_select.ev_planner_mode",
        CONF_ENTITY_PV_ROUNDING: "input_select.ev_pv_afronding",
        CONF_ENTITY_PRICES: "sensor.zonneplan_current_electricity_tariff",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
        CONF_MAX_CHARGE_POWER_KW: 11.04,
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_config_flow_creates_single_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input=config_data(),
    )

    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert result["title"] == "EV Charge Planner"
    assert result["data"] == config_data()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data == config_data()

    second = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    assert second["type"] == "abort"
    assert second["reason"] == "single_instance_allowed"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_flow_updates_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_data(),
        title="EV Charge Planner",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    updated = config_data()
    updated[CONF_MAX_CHARGE_POWER_KW] = 7.36

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=updated,
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_MAX_CHARGE_POWER_KW] == 7.36
