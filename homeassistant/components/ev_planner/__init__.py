"""Native Home Assistant integration for EV Charge Planner."""

from __future__ import annotations

from datetime import datetime as dt_datetime
import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .core.ev_planner import EVPlannerController
from .core.logger import Logger

_LOGGER = logging.getLogger(__name__)

SERVICE_NAMES = (
    "update",
    "create_plan",
    "replan",
    "clear_plan",
    "status",
    "dashboard",
)
RESPONSE_SERVICES = {"status", "dashboard"}

type EVPlannerConfigEntry = ConfigEntry[EVPlannerController]

SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})


def _make_logger() -> Logger:
    """Create the EV Charge Planner logger."""
    return Logger(
        _LOGGER.debug,
        _LOGGER.info,
        _LOGGER.warning,
        _LOGGER.error,
    )


def _get_controller(
    hass: HomeAssistant, call: ServiceCall
) -> EVPlannerController:
    """Return the controller for the targeted config entry."""
    entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError("EV Charge Planner config entry not found")
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("EV Charge Planner config entry is not loaded")
    return cast(EVPlannerConfigEntry, entry).runtime_data


async def _run(
    hass: HomeAssistant, controller: EVPlannerController, method: str
) -> Any:
    """Run a synchronous controller method outside the event loop."""
    func = getattr(controller, method)
    if method == "clear_plan":
        return await hass.async_add_executor_job(func)
    return await hass.async_add_executor_job(func, dt_datetime.now().astimezone())


async def _async_handle_update(call: ServiceCall) -> None:
    """Handle the update action."""
    await _run(call.hass, _get_controller(call.hass, call), "update")


async def _async_handle_create_plan(call: ServiceCall) -> None:
    """Handle the create_plan action."""
    await _run(call.hass, _get_controller(call.hass, call), "create_plan")


async def _async_handle_replan(call: ServiceCall) -> None:
    """Handle the replan action."""
    await _run(call.hass, _get_controller(call.hass, call), "replan")


async def _async_handle_clear_plan(call: ServiceCall) -> None:
    """Handle the clear_plan action."""
    await _run(call.hass, _get_controller(call.hass, call), "clear_plan")


async def _async_handle_status(call: ServiceCall) -> dict:
    """Handle the status action."""
    controller = _get_controller(call.hass, call)
    return await call.hass.async_add_executor_job(controller.get_status)


async def _async_handle_dashboard(call: ServiceCall) -> dict:
    """Handle the dashboard action."""
    controller = _get_controller(call.hass, call)
    return await call.hass.async_add_executor_job(
        controller.get_dashboard_data,
        dt_datetime.now().astimezone(),
    )


SERVICE_HANDLERS = {
    "update": _async_handle_update,
    "create_plan": _async_handle_create_plan,
    "replan": _async_handle_replan,
    "clear_plan": _async_handle_clear_plan,
    "status": _async_handle_status,
    "dashboard": _async_handle_dashboard,
}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up EV Charge Planner services."""
    for name in SERVICE_NAMES:
        hass.services.async_register(
            DOMAIN,
            name,
            SERVICE_HANDLERS[name],
            schema=SERVICE_SCHEMA,
            supports_response=(
                SupportsResponse.ONLY
                if name in RESPONSE_SERVICES
                else SupportsResponse.NONE
            ),
        )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: EVPlannerConfigEntry
) -> bool:
    """Set up EV Charge Planner from a config entry."""
    merged_config = {**entry.data, **entry.options}
    controller = EVPlannerController(
        hass=hass,
        logger=_make_logger(),
        config=merged_config,
        entry_id=entry.entry_id,
    )
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.info("EV Charge Planner native integratie geladen.")
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: EVPlannerConfigEntry
) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: EVPlannerConfigEntry
) -> bool:
    """Unload EV Charge Planner."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
