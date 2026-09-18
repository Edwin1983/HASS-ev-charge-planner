"""EV Charge Planner select platform."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ENTITY_PLANNER_MODE,
    CONF_ENTITY_PV_ROUNDING,
    DOMAIN,
    PLANNER_MODE_NORMAL,
    PLANNER_MODE_SOLAR_ONLY,
    PV_ROUNDING_DOWN,
    PV_ROUNDING_UP,
)


PLANNER_MODES = [
    PLANNER_MODE_NORMAL,
    PLANNER_MODE_SOLAR_ONLY,
]

PV_ROUNDING_OPTIONS = [
    PV_ROUNDING_DOWN,
    PV_ROUNDING_UP,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EV Planner select."""

    async_add_entities([
        EVPlannerModeSelect(hass=hass, entry=entry),
        EVPlannerPvRoundingSelect(hass=hass, entry=entry),
    ])


class EVPlannerModeSelect(SelectEntity, RestoreEntity):
    """Select the EV Planner operating mode."""

    _attr_has_entity_name = True
    _attr_name = "Planner mode"
    _attr_icon = "mdi:ev-station"
    _attr_options = PLANNER_MODES

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the planner mode select."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_planner_mode"
        self._attr_current_option = PLANNER_MODE_NORMAL

    @property
    def device_info(self) -> dict:
        """Return EV Planner device information."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "EV Planner",
            "manufacturer": "EV Planner",
            "model": "EV Smart Charging",
        }

    async def async_added_to_hass(self) -> None:
        """Restore the previous mode, with legacy input_select fallback."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state and last_state.state in PLANNER_MODES:
            self._attr_current_option = last_state.state
            return

        legacy_entity = self._entry.data.get(CONF_ENTITY_PLANNER_MODE)
        if legacy_entity:
            legacy_state = self._hass.states.get(legacy_entity)
            if legacy_state and legacy_state.state in PLANNER_MODES:
                self._attr_current_option = legacy_state.state

    async def async_select_option(self, option: str) -> None:
        """Set the planner mode."""
        if option not in PLANNER_MODES:
            return

        self._attr_current_option = option
        self.async_write_ha_state()


class EVPlannerPvRoundingSelect(SelectEntity, RestoreEntity):
    """Select the PV charging current rounding mode."""

    _attr_has_entity_name = True
    _attr_name = "PV charging current rounding"
    _attr_icon = "mdi:solar-power"
    _attr_options = PV_ROUNDING_OPTIONS

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the PV rounding select."""
        self._hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_pv_rounding"
        self._attr_current_option = PV_ROUNDING_DOWN

    @property
    def device_info(self) -> dict:
        """Return EV Planner device information."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "EV Planner",
            "manufacturer": "EV Planner",
            "model": "EV Smart Charging",
        }

    async def async_added_to_hass(self) -> None:
        """Restore the previous setting, with legacy input_select fallback."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state and last_state.state in PV_ROUNDING_OPTIONS:
            self._attr_current_option = last_state.state
            return

        legacy_entity = self._entry.data.get(CONF_ENTITY_PV_ROUNDING)
        if legacy_entity:
            legacy_state = self._hass.states.get(legacy_entity)
            if legacy_state and legacy_state.state in PV_ROUNDING_OPTIONS:
                self._attr_current_option = legacy_state.state

    async def async_select_option(self, option: str) -> None:
        """Set the PV rounding mode."""
        if option not in PV_ROUNDING_OPTIONS:
            return

        self._attr_current_option = option
        self.async_write_ha_state()
