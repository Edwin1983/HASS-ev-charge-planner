"""EV Planner switch platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store

from .const import DOMAIN


STORAGE_VERSION = 1
STORAGE_KEY = "ev_planner_smart_charging"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV Planner switches."""

    store = Store[dict[str, Any]](
        hass,
        STORAGE_VERSION,
        f"{STORAGE_KEY}_{entry.entry_id}",
    )

    stored_data = await store.async_load()

    if stored_data is None:
        stored_data = {
            "is_on": False,
        }

    async_add_entities(
        [
            EVPlannerSmartChargingSwitch(
                entry=entry,
                store=store,
                stored_data=stored_data,
            ),
        ]
    )


class EVPlannerSmartChargingSwitch(SwitchEntity):
    """EV Planner Smart Charging switch."""

    _attr_has_entity_name = True
    _attr_name = "Smart Charging"
    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        entry: ConfigEntry,
        store: Store[dict[str, Any]],
        stored_data: dict[str, Any],
    ) -> None:
        """Initialize the switch."""

        self._entry = entry
        self._store = store

        self._attr_is_on = bool(
            stored_data.get("is_on", False)
        )

        self._attr_unique_id = (
            f"{entry.entry_id}_smart_charging"
        )

    @property
    def device_info(self):
        """Return EV Planner device information."""

        return {
            "identifiers": {
                (DOMAIN, self._entry.entry_id),
            },
            "name": "EV Planner",
            "manufacturer": "EV Planner",
            "model": "EV Smart Charging",
        }

    @property
    def is_on(self) -> bool:
        """Return whether Smart Charging is enabled."""

        return self._attr_is_on

    async def async_turn_on(
        self,
        **kwargs,
    ) -> None:
        """Enable Smart Charging."""

        self._attr_is_on = True

        await self._store.async_save(
            {
                "is_on": True,
            }
        )

        self.async_write_ha_state()

    async def async_turn_off(
        self,
        **kwargs,
    ) -> None:
        """Disable Smart Charging."""

        self._attr_is_on = False

        await self._store.async_save(
            {
                "is_on": False,
            }
        )

        self.async_write_ha_state()

