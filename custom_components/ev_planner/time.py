"""EV Charge Planner time platform."""

from __future__ import annotations

from datetime import datetime, time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_ENTITY_DEPARTURE, DOMAIN, NATIVE_DEPARTURE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EV Charge Planner time entity."""
    async_add_entities([EVPlannerDepartureTime(hass=hass, entry=entry)])


class EVPlannerDepartureTime(TimeEntity, RestoreEntity):
    """Desired departure time."""

    _attr_has_entity_name = True
    _attr_translation_key = "departure_time"
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the departure time."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_DEPARTURE}"
        self._attr_native_value = time(23, 59)

    @property
    def device_info(self) -> dict:
        """Return EV Charge Planner device information."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "EV Charge Planner",
            "manufacturer": "EV Charge Planner",
            "model": "EV Smart Charging",
        }

    async def async_added_to_hass(self) -> None:
        """Restore the previous time, with legacy input_datetime fallback."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        restored = self._parse_state(last_state.state if last_state else None)

        if restored is not None:
            self._attr_native_value = restored
            return

        legacy_entity = self._entry.data.get(CONF_ENTITY_DEPARTURE)
        if legacy_entity:
            legacy_state = self._hass.states.get(legacy_entity)
            restored = self._parse_state(
                legacy_state.state if legacy_state else None
            )
            if restored is not None:
                self._attr_native_value = restored

    @staticmethod
    def _parse_state(value: str | None) -> time | None:
        """Parse a Home Assistant time-only state."""
        if not value:
            return None

        try:
            return time.fromisoformat(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value).time()
            except ValueError:
                return None

    async def async_set_value(self, value: time) -> None:
        """Set the desired departure time."""
        self._attr_native_value = value
        self.async_write_ha_state()
