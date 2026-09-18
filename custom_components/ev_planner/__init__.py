"""Native Home Assistant custom integration for the EV Planner."""

from __future__ import annotations

from datetime import datetime as dt_datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from .const import DOMAIN, PLATFORMS
from .core.ev_planner import EVPlannerController
from .core.logger import Logger


_LOGGER = logging.getLogger(__name__)


def _make_logger() -> Logger:
    """Create the EV Planner logger."""

    return Logger(
        _LOGGER.debug,
        _LOGGER.info,
        _LOGGER.warning,
        _LOGGER.error,
    )


async def _run(
    hass: HomeAssistant,
    controller: EVPlannerController,
    method: str,
):
    """
    Run a synchronous controller method in the executor.

    The EV Planner core is synchronous and therefore runs
    outside the Home Assistant event loop.
    """

    func = getattr(controller, method)

    if method == "clear_plan":
        return await hass.async_add_executor_job(func)

    return await hass.async_add_executor_job(
        func,
        dt_dt_datetime.now().astimezone(),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up EV Planner from a config entry."""

    # ------------------------------------------------------------------
    # Create controller
    #
    # entry.options overschrijft entry.data zodra de gebruiker de
    # instellingen via de options flow ("Configureren") heeft aangepast.
    # ------------------------------------------------------------------

    merged_config = {
        **entry.data,
        **entry.options,
    }

    controller = EVPlannerController(
        hass=hass,
        logger=_make_logger(),
        config=merged_config,
        entry_id=entry.entry_id,
    )

    # ------------------------------------------------------------------
    # Store controller
    # ------------------------------------------------------------------

    domain_data = hass.data.setdefault(
        DOMAIN,
        {},
    )

    domain_data[entry.entry_id] = {
        "controller": controller,
    }

    # ------------------------------------------------------------------
    # Platforms
    # ------------------------------------------------------------------

    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS,
    )

    # ------------------------------------------------------------------
    # Services
    #
    # Home Assistant bepaalt zelf wanneer deze worden aangeroepen.
    # De integratie start GEEN eigen periodieke update.
    # ------------------------------------------------------------------

    async def handle_update(
        call: ServiceCall,
    ):
        """Update the planner."""

        await _run(
            hass,
            controller,
            "update",
        )

    async def handle_create_plan(
        call: ServiceCall,
    ):
        """Create a new charging plan."""

        await _run(
            hass,
            controller,
            "create_plan",
        )

    async def handle_replan(
        call: ServiceCall,
    ):
        """Create a new plan from scratch."""

        await _run(
            hass,
            controller,
            "replan",
        )

    async def handle_clear_plan(
        call: ServiceCall,
    ):
        """Clear the current plan."""

        await _run(
            hass,
            controller,
            "clear_plan",
        )

    async def handle_status(
        call: ServiceCall,
    ):
        """Return planner status."""

        return await hass.async_add_executor_job(
            controller.get_status,
        )

    async def handle_dashboard(
        call: ServiceCall,
    ):
        """Return dashboard data."""

        return await hass.async_add_executor_job(
            controller.get_dashboard_data,
            datetime.now().astimezone(),
        )

    handlers = {
        "update": handle_update,
        "create_plan": handle_create_plan,
        "replan": handle_replan,
        "clear_plan": handle_clear_plan,
        "status": handle_status,
        "dashboard": handle_dashboard,
    }

    # Services die een dictionary teruggeven en dus response data
    # ondersteunen (bruikbaar met `response_variable` in scripts/
    # automations).
    RESPONSE_SERVICES = {
        "status",
        "dashboard",
    }

    # ------------------------------------------------------------------
    # Register services
    # ------------------------------------------------------------------

    for name, handler in handlers.items():
        if hass.services.has_service(
            DOMAIN,
            name,
        ):
            continue

        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            supports_response=(
                SupportsResponse.OPTIONAL
                if name in RESPONSE_SERVICES
                else SupportsResponse.NONE
            ),
        )

    # ------------------------------------------------------------------
    # Opties
    #
    # Herlaad de entry automatisch zodra de gebruiker instellingen
    # via de options flow ("Configureren") aanpast.
    # ------------------------------------------------------------------

    entry.async_on_unload(
        entry.add_update_listener(
            _async_update_listener
        )
    )

    _LOGGER.info(
        "EV Planner native integratie geladen."
    )

    return True


async def _async_update_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Herlaad de config entry wanneer de opties wijzigen."""

    await hass.config_entries.async_reload(
        entry.entry_id
    )


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload EV Planner."""

    # ------------------------------------------------------------------
    # Unload platforms
    # ------------------------------------------------------------------

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )

    if not unload_ok:
        return False

    # ------------------------------------------------------------------
    # Remove controller
    # ------------------------------------------------------------------

    domain_data = hass.data.get(
        DOMAIN,
        {},
    )

    domain_data.pop(
        entry.entry_id,
        None,
    )

    # ------------------------------------------------------------------
    # Remove services when last entry is unloaded
    # ------------------------------------------------------------------

    if not domain_data:
        for service in (
            "update",
            "create_plan",
            "replan",
            "clear_plan",
            "status",
            "dashboard",
        ):
            if hass.services.has_service(
                DOMAIN,
                service,
            ):
                hass.services.async_remove(
                    DOMAIN,
                    service,
                )

    return True
