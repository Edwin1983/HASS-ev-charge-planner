from __future__ import annotations

import datetime as dt

import pytest

from custom_components.ev_planner.const import (
    CONF_ENTITY_DEPARTURE,
    CONF_ENTITY_DEPARTURE_DAY,
    CONF_ENTITY_ENERGY_NEEDED,
    CONF_ENTITY_MAX_PHASE_SWITCHES,
    CONF_ENTITY_MAX_PRICE,
    CONF_ENTITY_MIN_PV_KWH,
    CONF_ENTITY_PV_ROUNDING,
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    CONF_MAX_CHARGE_POWER_KW,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def ignore_missing_translations(request: pytest.FixtureRequest) -> list[str]:
    """Ignore unrelated Home Assistant Core translation checks per test."""
    ignores = {
        "test_full_integration_setup_and_unload": [
            "component.select.services.select_last.name",
        ],
        "test_native_only_configuration": [
            "component.switch.services.turn_on.name",
        ],
        "test_full_planning_chain": [
            "component.switch.services.toggle.name",
        ],
        "test_service_rejects_unknown_config_entry": [
            "component.number.services.set_value.name",
        ],
        "test_service_rejects_unloaded_config_entry": [
            "component.switch.services.turn_on.name",
        ],
    }
    return ignores.get(request.node.name, [])


def make_config():
    return {
        CONF_ENTITY_DEPARTURE: "input_datetime.ev_vertrektijd",
        CONF_ENTITY_DEPARTURE_DAY: "input_select.ev_vertrekdag",
        CONF_ENTITY_ENERGY_NEEDED: "input_number.ev_kwh_nodig",
        CONF_ENTITY_MAX_PRICE: "input_number.ev_max_prijs",
        CONF_ENTITY_MIN_PV_KWH: "input_number.ev_min_pv_kwh",
        CONF_ENTITY_MAX_PHASE_SWITCHES: "input_number.ev_max_fasewisselingen",
        CONF_ENTITY_PV_ROUNDING: "input_select.ev_pv_afronding",
        CONF_ENTITY_PRICES: "sensor.zonneplan_current_electricity_tariff",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
        CONF_MAX_CHARGE_POWER_KW: 11.04,
    }


async def test_full_integration_setup_and_unload(
    hass: HomeAssistant, enable_custom_integrations: None
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Planner",
        data=make_config(),
        unique_id="test-entry",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not None
    assert hass.services.has_service(DOMAIN, "update")
    assert hass.services.has_service(DOMAIN, "create_plan")
    assert hass.services.has_service(DOMAIN, "replan")
    assert hass.services.has_service(DOMAIN, "clear_plan")
    assert hass.services.has_service(DOMAIN, "status")
    assert hass.services.has_service(DOMAIN, "dashboard")

    status_response = await hass.services.async_call(
        DOMAIN, "status", {"config_entry_id": entry.entry_id},
        blocking=True, return_response=True,
    )
    assert isinstance(status_response, dict)
    dashboard_response = await hass.services.async_call(
        DOMAIN, "dashboard", {"config_entry_id": entry.entry_id},
        blocking=True, return_response=True,
    )
    assert isinstance(dashboard_response, dict)

    states = hass.states
    for entity_id in (
        "sensor.ev_planner_state", "sensor.ev_planner_data",
        "sensor.ev_planner_desired_charge_current",
        "sensor.ev_planner_desired_phases",
        "binary_sensor.ev_planner_charging_allowed",
        "switch.ev_planner_smart_charging",
        "select.ev_charge_planner_departure_day",
        "select.ev_charge_planner_planner_mode",
        "select.ev_charge_planner_pv_charging_current_rounding",
        "time.ev_charge_planner_departure_time",
        "number.ev_charge_planner_energy_needed",
        "number.ev_charge_planner_maximum_grid_price",
        "number.ev_charge_planner_maximum_phase_switches",
        "number.ev_charge_planner_minimum_pv_for_solar_only",
        "number.ev_charge_planner_maximum_charging_power",
    ):
        assert states.get(entity_id) is not None

    energy_needed = states.get("number.ev_charge_planner_energy_needed")
    max_grid_price = states.get("number.ev_charge_planner_maximum_grid_price")
    min_pv = states.get("number.ev_charge_planner_minimum_pv_for_solar_only")
    assert energy_needed.attributes["mode"] == "slider"
    assert max_grid_price.attributes["mode"] == "slider"
    assert min_pv.attributes["mode"] == "slider"
    assert max_grid_price.attributes["unit_of_measurement"] == "ct/kWh"
    assert max_grid_price.attributes["step"] == 1.0
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "update")


async def test_native_only_configuration(
    hass: HomeAssistant, enable_custom_integrations: None
):
    """The integration can be set up without legacy input helpers."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Planner",
        data={
            CONF_ENTITY_PRICES: "sensor.zonneplan_current_electricity_tariff",
            CONF_ENTITY_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
            CONF_ENTITY_SOLCAST_TOMORROW: (
                "sensor.solcast_pv_forecast_forecast_tomorrow"
            ),
        },
        unique_id="native-only",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("time.ev_charge_planner_departure_time") is not None
    assert hass.states.get("select.ev_charge_planner_departure_day") is not None
    assert hass.states.get("number.ev_charge_planner_energy_needed") is not None
    assert hass.states.get("number.ev_charge_planner_maximum_grid_price") is not None
    assert hass.states.get(
        "number.ev_charge_planner_maximum_phase_switches"
    ) is not None
    assert hass.states.get(
        "number.ev_charge_planner_minimum_pv_for_solar_only"
    ) is not None
    assert hass.states.get(
        "number.ev_charge_planner_maximum_charging_power"
    ) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_full_planning_chain(
    hass: HomeAssistant, enable_custom_integrations: None
):
    """Exercise config -> readers -> planner -> scheduler -> native output."""
    now = dt.datetime.now().astimezone()
    base = now.replace(minute=0, second=0, microsecond=0)
    departure = base + dt.timedelta(hours=4)

    hass.states.async_set(
        "input_datetime.ev_vertrektijd", departure.strftime("%H:%M:%S")
    )
    hass.states.async_set(
        "input_select.ev_vertrekdag",
        "Vandaag" if departure.date() == base.date() else "Morgen",
    )
    hass.states.async_set("input_number.ev_kwh_nodig", "2.0")
    hass.states.async_set("input_number.ev_max_prijs", "0.20")
    hass.states.async_set("input_number.ev_min_pv_kwh", "0.0")
    hass.states.async_set("input_number.ev_max_fasewisselingen", "8")
    hass.states.async_set(
        "input_select.ev_pv_afronding", "Naar beneden — geen netenergie"
    )

    forecast = []
    solcast_today = []
    solcast_tomorrow = []
    for index in range(6):
        start = base + dt.timedelta(hours=index)
        forecast.append(
            {"start_date": start.isoformat(), "electricity_price": 1_000_000}
        )
        item = {
            "period_start": start.isoformat(),
            "pv_estimate": 0.0,
            "pv_estimate10": 0.0,
            "pv_estimate90": 0.0,
        }
        target = solcast_today if start.date() == base.date() else solcast_tomorrow
        target.append(item)

    hass.states.async_set(
        "sensor.zonneplan_current_electricity_tariff", "0.10", {"forecast": forecast}
    )
    hass.states.async_set(
        "sensor.solcast_pv_forecast_forecast_today", "0",
        {"detailedHourly": solcast_today},
    )
    hass.states.async_set(
        "sensor.solcast_pv_forecast_forecast_tomorrow", "0",
        {"detailedHourly": solcast_tomorrow},
    )

    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Planner", data=make_config(),
        unique_id="planning-chain",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.ev_planner_smart_charging"},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN, "update", {"config_entry_id": entry.entry_id}, blocking=True,
    )
    await hass.async_block_till_done()

    data_state = hass.states.get("sensor.ev_planner_data")
    assert data_state is not None
    assert data_state.attributes["decisions"]
    assert data_state.attributes["energy_planned_kwh"] > 0
    charge_current = hass.states.get("sensor.ev_planner_desired_charge_current")
    phases = hass.states.get("sensor.ev_planner_desired_phases")
    assert charge_current is not None
    assert phases is not None
    assert 0 <= int(charge_current.state) <= 16
    assert int(phases.state) in {0, 1, 3}
    state = hass.states.get("sensor.ev_planner_state")
    assert state is not None
    assert state.state not in {
        "Geen prijsdata", "Geen PV-data",
        "Fout bij plannen", "Ongeldige plannerinstellingen",
    }
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_service_rejects_unknown_config_entry(hass: HomeAssistant):
    """Service actions reject an unknown config entry."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "status", {"config_entry_id": "does-not-exist"},
            blocking=True, return_response=True,
        )


async def test_service_rejects_unloaded_config_entry(
    hass: HomeAssistant, enable_custom_integrations: None
):
    """Service actions reject a config entry that is not loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Planner", data=make_config(),
        unique_id="unloaded-entry",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "status", {"config_entry_id": entry.entry_id},
            blocking=True, return_response=True,
        )
