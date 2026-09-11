"""EV Planner sensor platform."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_SENSOR_UPDATE


DATA_SENSOR_STATE = "sensor_state"
DATA_SENSOR_DATA = "sensor_data"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV Planner sensors."""

    domain_data = hass.data.setdefault(
        DOMAIN,
        {},
    )

    entry_data = domain_data.setdefault(
        entry.entry_id,
        {},
    )

    entry_data.setdefault(
        DATA_SENSOR_STATE,
        "Geen actieve laadbeslissing",
    )

    entry_data.setdefault(
        DATA_SENSOR_DATA,
        {
            "state": "Geen planning",
            "attributes": {
                "energy_needed_kwh": 0.0,
                "energy_planned_kwh": 0.0,
                "missing_energy_kwh": 0.0,
                "free_energy_kwh": 0.0,
                "paid_energy_kwh": 0.0,
                "estimated_cost": 0.0,
                "complete": False,
                "departure_time": None,
                "max_price": 0.0,
                "charging_windows": 0,
                "phase_switches": 0,
                "max_phase_switches": 0,
                "total_charging_minutes": 0.0,
                "decisions": [],
            },
        },
    )

    async_add_entities(
        [
            EVPlannerStateSensor(
                hass=hass,
                entry=entry,
            ),
            EVPlannerDataSensor(
                hass=hass,
                entry=entry,
            ),
        ]
    )


class EVPlannerBaseSensor(SensorEntity):
    """Base class for EV Planner sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""

        self._hass = hass
        self._entry = entry

    @property
    def _entry_data(self) -> dict[str, Any]:
        """Return integration entry data."""

        domain_data = self._hass.data.get(
            DOMAIN,
            {},
        )

        return domain_data.get(
            self._entry.entry_id,
            {},
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
        """Update the sensor when planner data changes."""

        self.async_write_ha_state()


class EVPlannerStateSensor(EVPlannerBaseSensor):
    """Sensor containing the current EV Planner state."""

    _attr_name = "State"
    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the state sensor."""

        super().__init__(
            hass=hass,
            entry=entry,
        )

        self._attr_unique_id = (
            f"{entry.entry_id}_state"
        )

    @property
    def native_value(self) -> str:
        """Return the current planner state."""

        return str(
            self._entry_data.get(
                DATA_SENSOR_STATE,
                "Geen actieve laadbeslissing",
            )
        )


class EVPlannerDataSensor(EVPlannerBaseSensor):
    """Sensor containing EV Planner plan data."""

    _attr_name = "Data"
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the data sensor."""

        super().__init__(
            hass=hass,
            entry=entry,
        )

        self._attr_unique_id = (
            f"{entry.entry_id}_data"
        )

    @property
    def native_value(self) -> str:
        """Return the current plan state."""

        data = self._entry_data.get(
            DATA_SENSOR_DATA,
            {},
        )

        return str(
            data.get(
                "state",
                "Geen planning",
            )
        )

    @property
    def extra_state_attributes(
        self,
    ) -> dict[str, Any]:
        """Return planner data attributes."""

        data = self._entry_data.get(
            DATA_SENSOR_DATA,
            {},
        )

        attributes = data.get(
            "attributes",
            {},
        )

        return dict(attributes)
