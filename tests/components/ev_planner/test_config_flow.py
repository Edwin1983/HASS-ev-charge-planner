"""Config flow tests for the native EV Charge Planner integration."""

from __future__ import annotations

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from homeassistant.components.ev_planner.const import (
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    DOMAIN,
)
from tests.common import MockConfigEntry


@pytest.fixture
def valid_config() -> dict[str, str]:
    """Return valid config-flow input."""
    return {
        CONF_ENTITY_PRICES: "sensor.test_prices",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.test_solcast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.test_solcast_tomorrow",
    }


async def test_form_success(hass: HomeAssistant, valid_config: dict[str, str]) -> None:
    """Test the config flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input=valid_config,
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "EV Charge Planner"
    assert result["data"] == valid_config


async def test_single_instance(
    hass: HomeAssistant, valid_config: dict[str, str]
) -> None:
    """Test only one config entry can be created."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Charge Planner", data=valid_config
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(
    hass: HomeAssistant, valid_config: dict[str, str]
) -> None:
    """Test the options flow."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Charge Planner", data=valid_config
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=valid_config,
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == valid_config


async def test_reconfigure(
    hass: HomeAssistant, valid_config: dict[str, str]
) -> None:
    """Test reconfiguring the planner input entities."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Charge Planner", data=valid_config
    )
    entry.add_to_hass(hass)

    new_config = {
        **valid_config,
        CONF_ENTITY_PRICES: "sensor.new_electricity_prices",
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input=new_config,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert updated_entry.data[CONF_ENTITY_PRICES] == new_config[CONF_ENTITY_PRICES]
