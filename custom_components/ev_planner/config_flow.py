from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CHARGER_DEFAULTS,
    CONF_CHARGER_CHARGING_STATES,
    CONF_CHARGER_CONNECTED_STATES,
    CONF_CHARGER_CURRENT_ENTITY,
    CONF_CHARGER_ENERGY_ENTITY,
    CONF_CHARGER_MODE_ENTITY,
    CONF_CHARGER_PHASE_ENTITY,
    CONF_CHARGER_PHASE_ONE_VALUE,
    CONF_CHARGER_PHASE_THREE_VALUE,
    CONF_CHARGER_START_ENTITY,
    CONF_CHARGER_STARTABLE_STATES,
    CONF_CHARGER_STOP_ENTITY,
    CONF_ENTITY_PRICES,
    CONF_ENTITY_PRICE_FORECAST,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    DEFAULT_ENTITY_PRICES,
    DEFAULT_ENTITY_PRICE_FORECAST,
    DEFAULT_ENTITY_SOLCAST_TODAY,
    DEFAULT_ENTITY_SOLCAST_TOMORROW,
    DOMAIN,
)


_DEFAULTS = {
    CONF_ENTITY_PRICES: DEFAULT_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY: DEFAULT_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW: DEFAULT_ENTITY_SOLCAST_TOMORROW,
}


def _forecast_field(current: dict) -> dict:
    """Optionele sensor met voorspelde prijzen (leeg = uitgeschakeld)."""
    value = current.get(CONF_ENTITY_PRICE_FORECAST, DEFAULT_ENTITY_PRICE_FORECAST)
    selector_ = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
    if not value:
        return {vol.Optional(CONF_ENTITY_PRICE_FORECAST): selector_}
    return {
        vol.Optional(
            CONF_ENTITY_PRICE_FORECAST,
            description={"suggested_value": value},
        ): selector_
    }


def _build_schema(defaults: dict[str, str]) -> vol.Schema:
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


CHARGER_KEYS = (
    CONF_CHARGER_MODE_ENTITY,
    CONF_CHARGER_CONNECTED_STATES,
    CONF_CHARGER_CHARGING_STATES,
    CONF_CHARGER_STARTABLE_STATES,
    CONF_CHARGER_CURRENT_ENTITY,
    CONF_CHARGER_START_ENTITY,
    CONF_CHARGER_STOP_ENTITY,
    CONF_CHARGER_PHASE_ENTITY,
    CONF_CHARGER_PHASE_ONE_VALUE,
    CONF_CHARGER_PHASE_THREE_VALUE,
    CONF_CHARGER_ENERGY_ENTITY,
)


def _charger_fields(current: dict) -> dict:
    """Optionele velden voor de laadregeling (alleen in de opties)."""

    def entity(domain):
        return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))

    def text():
        return selector.TextSelector()

    def number():
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-1000,
                max=1000,
                step="any",
                mode=selector.NumberSelectorMode.BOX,
            )
        )

    # Eerste keer (nog geen enkel laderveld opgeslagen): vul de voorgestelde
    # waarden in. Daarna alleen wat de gebruiker heeft opgeslagen, zodat een
    # bewust leeggemaakt veld leeg blijft.
    if not any(key in current for key in CHARGER_KEYS):
        current = {**CHARGER_DEFAULTS, **current}

    def optional(key, sel):
        # suggested_value (geen default), zodat een veld leeg gemaakt kan worden
        value = current.get(key)
        if value is None:
            return {vol.Optional(key): sel}
        return {vol.Optional(key, description={"suggested_value": value}): sel}

    fields: dict = {}
    for key, sel in (
        (CONF_CHARGER_MODE_ENTITY, entity("sensor")),
        (CONF_CHARGER_CONNECTED_STATES, text()),
        (CONF_CHARGER_CHARGING_STATES, text()),
        (CONF_CHARGER_STARTABLE_STATES, text()),
        (CONF_CHARGER_CURRENT_ENTITY, entity("number")),
        (CONF_CHARGER_START_ENTITY, entity("switch")),
        (CONF_CHARGER_STOP_ENTITY, entity(["button", "switch"])),
        (CONF_CHARGER_PHASE_ENTITY, entity("number")),
        (CONF_CHARGER_PHASE_ONE_VALUE, number()),
        (CONF_CHARGER_PHASE_THREE_VALUE, number()),
        (CONF_CHARGER_ENERGY_ENTITY, entity("sensor")),
    ):
        fields.update(optional(key, sel))
    return fields


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

    async def async_step_reconfigure(
        self,
        user_input: dict[str, str] | None = None,
    ) -> FlowResult:
        """Handle reconfiguration of the planner input entities."""
        if user_input is not None:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates=user_input,
            )

        current = self._get_reconfigure_entry().data
        defaults = {
            key: current.get(key, default)
            for key, default in _DEFAULTS.items()
        }

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_schema(defaults),
        )


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
            data = dict(user_input)
            # Een leeggemaakt veld ontbreekt in de invoer; sla dat expliciet
            # op, anders zou de standaardwaarde de voorspelling weer aanzetten.
            data.setdefault(CONF_ENTITY_PRICE_FORECAST, "")
            return self.async_create_entry(title="", data=data)

        current = {
            **self.config_entry.data,
            **self.config_entry.options,
        }
        defaults = {
            key: current.get(key, default)
            for key, default in _DEFAULTS.items()
        }

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(defaults)
            .extend(_forecast_field(current))
            .extend(_charger_fields(current)),
        )
