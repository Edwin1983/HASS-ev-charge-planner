"""Config flow tests for EV Charge Planner."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_PLATFORM
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ev_planner.config_flow import EVPlannerConfigFlow
from custom_components.ev_planner.const import (
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    DOMAIN,
)


async def test_form_success(hass, valid_config):
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
    assert result["data"][CONF_ENTITY_PRICES] == valid_config[CONF_ENTITY_PRICES]
    assert result["data"][CONF_ENTITY_SOLCAST_TODAY] == valid_config[CONF_ENTITY_SOLCAST_TODAY]
    assert result["data"][CONF_ENTITY_SOLCAST_TOMORROW] == valid_config[CONF_ENTITY_SOLCAST_TOMORROW]


async def test_single_instance(hass, config_entry):
    """Test only one config entry can be created."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(hass, config_entry, valid_config):
    """Test the options flow."""

    result = await hass.config_entries.options.async_init(config_entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=valid_config,
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == valid_config
