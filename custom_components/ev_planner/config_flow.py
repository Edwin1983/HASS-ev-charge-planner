from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    DEFAULT_ENTITY_PRICES,
    DEFAULT_ENTITY_SOLCAST_TODAY,
    DEFAULT_ENTITY_SOLCAST_TOMORROW,
    DOMAIN,
)


_DEFAULTS = {
    CONF_ENTITY_PRICES: DEFAULT_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY: DEFAULT_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW: DEFAULT_ENTITY_SOLCAST_TOMORROW,
}


def _build_schema(defaults: dict) -> vol.Schema:
    """Build the EV Charge Planner configuration schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_ENTITY_PRICES,
                default=defaults[CONF_ENTITY_PRICES],
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_ENTITY_SOLCAST_TODAY,
                default=defaults[CONF_ENTITY_SOLCAST_TODAY],
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_ENTITY_SOLCAST_TOMORROW,
                default=defaults[CONF_ENTITY_SOLCAST_TOMORROW],
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
        }
    )


class EVPlannerConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Config flow for EV Charge Planner."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, str] | None = None,
    ) -> FlowResult:
        """Create the EV Charge Planner integration."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(
                title="EV Charge Planner",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(_DEFAULTS),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "EVPlannerOptionsFlow":
        """Return the options flow for this entry."""
        return EVPlannerOptionsFlow(config_entry)


class EVPlannerOptionsFlow(config_entries.OptionsFlow):
    """Options flow for EV Charge Planner."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, str] | None = None,
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {
            **self._config_entry.data,
            **self._config_entry.options,
        }
        defaults = {
            key: current.get(key, default)
            for key, default in _DEFAULTS.items()
        }

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(defaults),
        )
