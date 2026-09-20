"""EV Planner sensor platform."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_SENSOR_UPDATE



def _current_decision(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the planner decision active at the current time."""

    decisions = data.get("attributes", {}).get("decisions", [])
    now = datetime.now().astimezone()

    for decision in decisions:
        try:
            start = datetime.fromisoformat(str(decision.get("start")))
            end = datetime.fromisoformat(str(decision.get("end")))
        except (TypeError, ValueError):
            continue

        if start <= now < end:
            return decision

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV Planner sensors."""

    async_add_entities(
        [
            EVPlannerStateSensor(hass=hass, entry=entry),
            EVPlannerDataSensor(hass=hass, entry=entry),
            EVPlannerChargeCurrentSensor(hass=hass, entry=entry),
            EVPlannerPhasesSensor(hass=hass, entry=entry),
        ]
    )


class EVPlannerBaseSensor(SensorEntity):
    """Base class for EV Planner sensors."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the sensor."""

        self._hass = hass
        self._entry = entry

    @property
    def _entry_data(self) -> dict[str, Any]:
        """Return integration entry data."""

        return self._entry.runtime_data

    @property
    def device_info(self) -> dict[str, Any]:
        """Return EV Planner device information."""

        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
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

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the state sensor."""

        super().__init__(hass=hass, entry=entry)
        self._attr_unique_id = f"{entry.entry_id}_state"

    @property
    def native_value(self) -> str:
        """Return the current planner state."""

        return str(
            self._entry_data.sensor_state
        )


class EVPlannerDataSensor(EVPlannerBaseSensor):
    """Sensor containing EV Planner plan data."""

    _attr_name = "Data"
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the data sensor."""

        super().__init__(hass=hass, entry=entry)
        self._attr_unique_id = f"{entry.entry_id}_data"

    @property
    def native_value(self) -> str:
        """Return the current plan state."""

        data = self._entry_data.sensor_data
        return str(data.get("state", "Geen planning"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return planner data attributes."""

        data = self._entry_data.sensor_data
        return dict(data.get("attributes", {}))


class EVPlannerChargeCurrentSensor(EVPlannerBaseSensor):
    """Sensor containing the desired charging current right now."""

    _attr_name = "Gewenste laadstroom"
    _attr_icon = "mdi:ev-station"
    _attr_native_unit_of_measurement = "A"
    _attr_device_class = SensorDeviceClass.CURRENT

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the desired charging current sensor."""

        super().__init__(hass=hass, entry=entry)
        self._attr_unique_id = f"{entry.entry_id}_desired_charge_current"

    @property
    def native_value(self) -> int:
        """Return the desired charging current at this moment."""

        data = self._entry_data.sensor_data
        decision = _current_decision(data)
        if decision is None:
            return 0
        return int(decision.get("charge_current_a", 0))


class EVPlannerPhasesSensor(EVPlannerBaseSensor):
    """Sensor containing the desired number of charging phases right now."""

    _attr_name = "Gewenste fase"
    _attr_icon = "mdi:sine-wave"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the desired phases sensor."""

        super().__init__(hass=hass, entry=entry)
        self._attr_unique_id = f"{entry.entry_id}_desired_phases"

    @property
    def native_value(self) -> int:
        """Return the desired number of charging phases at this moment."""

        data = self._entry_data.sensor_data
        decision = _current_decision(data)
        if decision is None:
            return 0
        return int(decision.get("phases", 0))
