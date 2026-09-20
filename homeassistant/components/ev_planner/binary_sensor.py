"""EV Planner binary_sensor platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_SENSOR_UPDATE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV Planner binary sensors."""

    async_add_entities(
        [
            EVPlannerChargingAllowedBinarySensor(
                hass=hass,
                entry=entry,
            ),
        ]
    )


class EVPlannerChargingAllowedBinarySensor(BinarySensorEntity):
    """
    Alleen-lezen weergave van de plannerbeslissing.

    Deze entity geeft weer of de planner op dit moment laden
    toestaat (scheduler.charging_allowed). Het is GEEN
    besturingsinput en heeft geen invloed op de planner.
    """

    _attr_has_entity_name = True
    _attr_name = "Charging Allowed"
    _attr_icon = "mdi:car-electric"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the binary sensor."""

        self._hass = hass
        self._entry = entry

        self._attr_unique_id = (
            f"{entry.entry_id}_charging_allowed"
        )

    @property
    def device_info(self) -> dict[str, Any]:
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
    def _entry_data(self) -> Any:
        """Return integration entry data."""

        return self._entry.runtime_data

    @property
    def is_on(self) -> bool:
        """Return whether the planner currently allows charging."""

        return bool(self._entry_data.charging_allowed)

    async def async_added_to_hass(self) -> None:
        """Register dispatcher listener."""

        await super().async_added_to_hass()

        self.async_on_remove(
            async_dispatcher_connect(
                self._hass,
                SIGNAL_SENSOR_UPDATE,
                self._handle_sensor_update,
            )
        )

    async def _handle_sensor_update(self) -> None:
        """Update the entity when planner data changes."""

        self.async_write_ha_state()
