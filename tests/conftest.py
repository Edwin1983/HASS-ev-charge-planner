"""Shared pytest fixtures for EV Charge Planner tests."""

pytest_plugins = "pytest_homeassistant_custom_component"

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_planner.const import (
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    DOMAIN,
)


@pytest.fixture
def valid_config():
    """Return valid config-flow input."""

    return {
        CONF_ENTITY_PRICES: "sensor.test_prices",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.test_solcast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.test_solcast_tomorrow",
    }


@pytest.fixture
def config_entry(hass, valid_config):
    """Create a config entry for the integration."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Planner",
        data=valid_config,
    )
    entry.add_to_hass(hass)
    return entry
