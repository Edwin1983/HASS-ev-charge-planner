from __future__ import annotations

import pytest

from homeassistant.config_entries import SOURCE_USER
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_planner.const import (
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    DOMAIN,
)


def config_data():
    return {
        CONF_ENTITY_PRICES: "sensor.zonneplan_current_electricity_tariff",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_config_flow_creates_single_entry(hass):
    """Test that only external data sources are configured."""
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
    """Test updating the external data-source entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Planner",
        data=config_data(),
        unique_id="options-test",
    )
    hass.config_entries.async_add(entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    updated = {
        **config_data(),
        CONF_ENTITY_PRICES: "sensor.other_price_sensor",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=updated,
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_ENTITY_PRICES] == "sensor.other_price_sensor"
