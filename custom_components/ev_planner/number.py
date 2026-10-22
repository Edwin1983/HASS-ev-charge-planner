"""EV Charge Planner number platform."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
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
    DEFAULT_PROGRESS_THRESHOLD_KWH,
    FORECAST_PRICE_MARKUP,
    DEFAULT_EXTRA_CHARGE_PRICE_CT,
    NATIVE_EXTRA_CHARGE_PRICE,
    NATIVE_FORECAST_MARKUP,
    NATIVE_MAX_ENERGY_NEEDED,
    NATIVE_MAX_PAUSES,
    NATIVE_PROGRESS_THRESHOLD,
    MAX_PAUSES_UNLIMITED,
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
            EVPlannerMaxPauses(hass, entry),
            EVPlannerProgressThreshold(hass, entry),
            EVPlannerForecastMarkup(hass, entry),
            EVPlannerMaxEnergyNeeded(hass, entry),
            EVPlannerExtraChargePrice(hass, entry),
            EVPlannerMinPv(hass, entry),
            EVPlannerMaxChargePower(hass, entry),
        ]
    )


class EVPlannerNumberBase(NumberEntity, RestoreEntity):
    """Base class for EV Charge Planner number entities."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

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

    _attr_translation_key = "energy_needed"
    _attr_icon = "mdi:battery-plus"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_ENERGY_NEEDED}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(10.0, CONF_ENTITY_ENERGY_NEEDED)


class EVPlannerMaxEnergyNeeded(EVPlannerNumberBase):
    """Maximum charging energy (0 = same as the minimum)."""

    _attr_translation_key = "max_energy_needed"
    _attr_icon = "mdi:battery-plus-outline"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MAX_ENERGY_NEEDED}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(0.0, "")


class EVPlannerExtraChargePrice(EVPlannerNumberBase):
    """Price limit (ct/kWh) for charging beyond the minimum."""

    _attr_translation_key = "extra_charge_price"
    _attr_icon = "mdi:cash-fast"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 50.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_EXTRA_CHARGE_PRICE}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(DEFAULT_EXTRA_CHARGE_PRICE_CT, "")


class EVPlannerMaxPrice(EVPlannerNumberBase):
    """Maximum grid price."""

    _attr_translation_key = "maximum_grid_price"
    _attr_icon = "mdi:cash"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MAX_PRICE}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                value = float(last_state.state)
                unit = last_state.attributes.get("unit_of_measurement")
                if unit == "€/kWh":
                    value *= 100.0
                self._attr_native_value = value
                return
            except (TypeError, ValueError):
                pass

        legacy_entity = self._entry.data.get(CONF_ENTITY_MAX_PRICE)
        if legacy_entity:
            state = self._hass.states.get(legacy_entity)
            if state is not None:
                try:
                    self._attr_native_value = float(state.state) * 100.0
                    return
                except (TypeError, ValueError):
                    pass

        self._attr_native_value = 0.0


class EVPlannerMaxPhaseSwitches(EVPlannerNumberBase):
    """Maximum phase switches."""

    _attr_translation_key = "maximum_phase_switches"
    _attr_icon = "mdi:swap-horizontal"
    _attr_native_min_value = 0
    # De planner begrenst dit intern op 8 (MAX_PHASE_SWITCHES_HARD_CAP).
    _attr_native_max_value = 8
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "switches"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MAX_PHASE_SWITCHES}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(8.0, CONF_ENTITY_MAX_PHASE_SWITCHES)


class EVPlannerMaxPauses(EVPlannerNumberBase):
    """Maximum number of pauses between charging periods."""

    _attr_translation_key = "maximum_pauses"
    _attr_icon = "mdi:pause-circle-outline"
    _attr_native_min_value = 0
    # De hoogste waarde betekent: geen limiet.
    _attr_native_max_value = MAX_PAUSES_UNLIMITED
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "pauses"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MAX_PAUSES}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(float(MAX_PAUSES_UNLIMITED), "")


class EVPlannerProgressThreshold(EVPlannerNumberBase):
    """Deviation (kWh) during charging at which the charge control re-plans."""

    _attr_translation_key = "progress_threshold"
    _attr_icon = "mdi:chart-line-variant"
    _attr_native_min_value = 0.1
    _attr_native_max_value = 3.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_PROGRESS_THRESHOLD}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(DEFAULT_PROGRESS_THRESHOLD_KWH, "")


class EVPlannerForecastMarkup(EVPlannerNumberBase):
    """Markup (ct/kWh) added to forecast prices."""

    _attr_translation_key = "forecast_markup"
    _attr_icon = "mdi:cash-plus"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 20.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_mode = NumberMode.BOX

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_FORECAST_MARKUP}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(FORECAST_PRICE_MARKUP * 100, "")


class EVPlannerMinPv(EVPlannerNumberBase):
    """Minimum PV energy for solar-only mode."""

    _attr_translation_key = "minimum_pv_solar_only"
    _attr_icon = "mdi:solar-power"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 5.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{NATIVE_MIN_PV_KWH}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._restore_or_legacy(0.0, CONF_ENTITY_MIN_PV_KWH)


class EVPlannerMaxChargePower(EVPlannerNumberBase):
    """Maximum charging power used by the planner."""

    _attr_translation_key = "maximum_charging_power"
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
