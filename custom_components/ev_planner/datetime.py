"""EV Charge Planner datetime platform."""

from __future__ import annotations

from datetime import datetime, time

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_ENTITY_DEPARTURE, DOMAIN, NATIVE_DEPARTURE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EV Charge Planner datetime entity."""
    async_add_entities([EVPlannerDepartureDateTime(hass=hass, entry=entry)])


class EVPlannerDepartureDateTime(DateTimeEntity, RestoreEntity):
    """Desired departure time."""

    _attr_has_entity_name = True
    _attr_name = "Departure time"
    _attr_icon = "mdi:calendar-clock"
    _attr_has_date = False
    _attr_has_time = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the departure time."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_DEPARTURE}"
        now = datetime.now().astimezone()
        self._attr_native_value = datetime.combine(
            now.date(),
            time(23, 59),
            tzinfo=now.tzinfo,
        )

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
    def _parse_state(value: str | None) -> datetime | None:
        """Parse a Home Assistant time-only state."""
        if not value:
            return None

        try:
            parsed = time.fromisoformat(value)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(value).time()
            except ValueError:
                return None

        now = datetime.now().astimezone()
        return datetime.combine(now.date(), parsed, tzinfo=now.tzinfo)

    async def async_set_value(self, value: datetime) -> None:
        """Set the desired departure time."""
        self._attr_native_value = value
        self.async_write_ha_state()
