from __future__ import annotations

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_planner.config_flow import (
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
)
from custom_components.ev_planner.const import DOMAIN


VALID_INPUT = {
    CONF_ENTITY_PRICES: "sensor.test_prices",
    CONF_ENTITY_SOLCAST_TODAY: "sensor.test_solcast_today",
    CONF_ENTITY_SOLCAST_TOMORROW: "sensor.test_solcast_tomorrow",
}


async def test_user_flow_shows_form_and_creates_entry(hass: HomeAssistant):
    """The user flow shows the input form and creates one config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=VALID_INPUT
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "EV Charge Planner"
    assert result["data"] == VALID_INPUT


async def test_user_flow_uses_default_values(hass: HomeAssistant):
    """The user form exposes the configured default entities."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    schema = result["data_schema"]
    fields = {str(key.schema): key for key in schema.schema}

    assert fields[CONF_ENTITY_PRICES].default() == (
        "sensor.zonneplan_current_electricity_tariff"
    )
    assert fields[CONF_ENTITY_SOLCAST_TODAY].default() == (
        "sensor.solcast_pv_forecast_forecast_today"
    )
    assert fields[CONF_ENTITY_SOLCAST_TOMORROW].default() == (
        "sensor.solcast_pv_forecast_forecast_tomorrow"
    )


async def test_user_flow_aborts_when_entry_exists(hass: HomeAssistant):
    """Only one EV Charge Planner config entry is allowed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Planner",
        data=VALID_INPUT,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_flow_updates_entry(hass: HomeAssistant):
    """The reconfigure flow updates the configured input entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Planner",
        data=VALID_INPUT,
    )
    entry.add_to_hass(hass)

    new_input = {
        **VALID_INPUT,
        CONF_ENTITY_PRICES: "sensor.other_prices",
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=new_input
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    assert entry.data == new_input


async def test_options_flow_shows_current_values_and_updates_options(
    hass: HomeAssistant,
):
    """The options flow shows current values and stores changed options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Planner",
        data=VALID_INPUT,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    new_input = {
        **VALID_INPUT,
        CONF_ENTITY_SOLCAST_TODAY: "sensor.other_solcast_today",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=new_input
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == new_input


async def test_options_flow_prefers_existing_options(
    hass: HomeAssistant,
):
    """Options values override the original config entry data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Planner",
        data=VALID_INPUT,
        options={
            CONF_ENTITY_PRICES: "sensor.option_prices",
            CONF_ENTITY_SOLCAST_TODAY: "sensor.option_today",
            CONF_ENTITY_SOLCAST_TOMORROW: "sensor.option_tomorrow",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    schema = result["data_schema"]
    fields = {str(key.schema): key for key in schema.schema}

    assert fields[CONF_ENTITY_PRICES].default() == "sensor.option_prices"
    assert fields[CONF_ENTITY_SOLCAST_TODAY].default() == "sensor.option_today"
    assert fields[CONF_ENTITY_SOLCAST_TOMORROW].default() == "sensor.option_tomorrow"
