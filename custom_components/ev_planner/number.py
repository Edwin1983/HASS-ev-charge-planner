"""EV Charge Planner number platform."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_ENTITY_ENERGY_NEEDED,
    CONF_ENTITY_MAX_PHASE_SWITCHES,
    CONF_ENTITY_MAX_PRICE,
    CONF_ENTITY_MIN_PV_KWH,
    CONF_MAX_CHARGE_POWER_KW,
    DEFAULT_MAX_CHARGE_POWER_KW,
    DOMAIN,
    NATIVE_ENERGY_NEEDED,
    NATIVE_MAX_CHARGE_POWER,
    NATIVE_MAX_PHASE_SWITCHES,
    NATIVE_MAX_PRICE,
    NATIVE_MIN_PV_KWH,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV Charge Planner number entities."""
    async_add_entities(
        [
            EVPlannerEnergyNeeded(hass, entry),
            EVPlannerMaxPrice(hass, entry),
            EVPlannerMaxPhaseSwitches(hass, entry),
            EVPlannerMinPv(hass, entry),
            EVPlannerMaxChargePower(hass, entry),
        ]
    )


class EVPlannerNumberBase(NumberEntity, RestoreEntity):
    """Base class for EV Charge Planner number entities."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the number."""
        self._hass = hass
        self._entry = entry

    @property
    def device_info(self) -> dict:
        """Return EV Charge Planner device information."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "EV Charge Planner",
            "manufacturer": "EV Charge Planner",
            "model": "EV Smart Charging",
        }

    async def _restore_or_legacy(self, default: float, legacy_key: str) -> None:
        """Restore the value, falling back to the legacy input_number."""
        last_state = await self.async_get_last_state()

        if last_state is not None:
            try:
                self._attr_native_value = float(last_state.state)
                return
            except (TypeError, ValueError):
                pass

        legacy_entity = self._entry.data.get(legacy_key)
        if legacy_entity:
            state = self._hass.states.get(legacy_entity)
            if state is not None:
                try:
                    self._attr_native_value = float(state.state)
                    return
                except (TypeError, ValueError):
                    pass

        self._attr_native_value = default

    async def async_set_native_value(self, value: float) -> None:
        """Set the number value."""
        self._attr_native_value = float(value)
        self.async_write_ha_state()


class EVPlannerEnergyNeeded(EVPlannerNumberBase):
    """Required charging energy."""

    _attr_name = "Energy needed"
    _attr_icon = "mdi:battery-plus"
    _attr_native_min_value = 0.1
    _attr_native_max_value = 100.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_ENERGY_NEEDED}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(10.0, CONF_ENTITY_ENERGY_NEEDED)


class EVPlannerMaxPrice(EVPlannerNumberBase):
    """Maximum grid price."""

    _attr_name = "Maximum grid price"
    _attr_icon = "mdi:cash"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 5.0
    _attr_native_step = 0.001
    _attr_native_unit_of_measurement = "€/kWh"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MAX_PRICE}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(0.0, CONF_ENTITY_MAX_PRICE)


class EVPlannerMaxPhaseSwitches(EVPlannerNumberBase):
    """Maximum phase switches."""

    _attr_name = "Maximum phase switches"
    _attr_icon = "mdi:swap-horizontal"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "switches"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MAX_PHASE_SWITCHES}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(8.0, CONF_ENTITY_MAX_PHASE_SWITCHES)


class EVPlannerMinPv(EVPlannerNumberBase):
    """Minimum PV energy for solar-only mode."""

    _attr_name = "Minimum PV for solar-only"
    _attr_icon = "mdi:solar-power"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MIN_PV_KWH}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(0.0, CONF_ENTITY_MIN_PV_KWH)


class EVPlannerMaxChargePower(EVPlannerNumberBase):
    """Maximum charging power used by the planner."""

    _attr_name = "Maximum charging power"
    _attr_icon = "mdi:flash"
    _attr_native_min_value = 1.0
    _attr_native_max_value = 11.04
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "kW"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MAX_CHARGE_POWER}"

    async def async_added_to_hass(self) -> None:
        """Restore the value, with the old config option as fallback."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            try:
                self._attr_native_value = float(last_state.state)
                return
            except (TypeError, ValueError):
                pass

        legacy_value = self._entry.options.get(
            CONF_MAX_CHARGE_POWER_KW,
            self._entry.data.get(
                CONF_MAX_CHARGE_POWER_KW,
                DEFAULT_MAX_CHARGE_POWER_KW,
            ),
        )

        try:
            self._attr_native_value = float(legacy_value)
        except (TypeError, ValueError):
            self._attr_native_value = DEFAULT_MAX_CHARGE_POWER_KW
