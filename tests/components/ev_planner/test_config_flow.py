"""Config flow tests for the native EV Charge Planner integration."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from homeassistant.components.ev_planner.config_flow import EVPlannerConfigFlow
from homeassistant.components.ev_planner.const import (
    CONF_ENTITY_PRICES,
    CONF_ENTITY_SOLCAST_TODAY,
    CONF_ENTITY_SOLCAST_TOMORROW,
    DOMAIN,
)
from homeassistant.components.ev_planner.core.logger import Logger
from homeassistant.components.ev_planner.core.prices import PriceReader
from tests.common import MockConfigEntry


@pytest.fixture
def valid_config() -> dict[str, str]:
    """Return valid config-flow input."""
    return {
        CONF_ENTITY_PRICES: "sensor.test_prices",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.test_solcast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.test_solcast_tomorrow",
    }


async def test_form_success(hass: HomeAssistant, valid_config: dict[str, str]) -> None:
    """Test the config flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=valid_config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "EV Charge Planner"
    assert result["data"] == valid_config


async def test_single_instance(
    hass: HomeAssistant, valid_config: dict[str, str]
) -> None:
    """Test only one config entry can be created."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Charge Planner", data=valid_config
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_single_instance_branch_direct(
    valid_config: dict[str, str],
) -> None:
    """Test the single-instance branch directly."""
    flow = EVPlannerConfigFlow()
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Charge Planner", data=valid_config
    )
    flow._async_current_entries = lambda: (entry,)  # type: ignore[method-assign]
    result = await flow.async_step_user()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(
    hass: HomeAssistant, valid_config: dict[str, str]
) -> None:
    """Test the options flow."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Charge Planner", data=valid_config
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=valid_config
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == valid_config


async def test_reconfigure(
    hass: HomeAssistant, valid_config: dict[str, str]
) -> None:
    """Test reconfiguring the planner input entities."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="EV Charge Planner", data=valid_config
    )
    entry.add_to_hass(hass)
    new_config = {
        **valid_config,
        CONF_ENTITY_PRICES: "sensor.new_electricity_prices",
    }
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as mock_reload:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=new_config
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    mock_reload.assert_awaited_once_with(entry.entry_id)
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert updated_entry.data[CONF_ENTITY_PRICES] == new_config[CONF_ENTITY_PRICES]


async def test_core_prices_paths() -> None:
    """Cover PriceReader validation, parsing, filtering and statistics."""
    logger = Logger()
    now = dt.datetime.now().astimezone()
    base = now.replace(minute=0, second=0, microsecond=0)
    app = SimpleNamespace(
        get_attributes=lambda entity_id: {"forecast": []},
        get_state=lambda entity_id: "0.25",
    )
    reader = PriceReader(app, logger, "sensor.test_prices")

    with pytest.raises(RuntimeError, match="geen dictionary"):
        PriceReader(
            SimpleNamespace(get_attributes=lambda entity_id: None), logger
        )._read_forecast()
    with pytest.raises(RuntimeError, match="Forecast attribuut ontbreekt"):
        PriceReader(
            SimpleNamespace(get_attributes=lambda entity_id: {}), logger
        )._read_forecast()
    with pytest.raises(RuntimeError, match="Forecast is geen lijst"):
        PriceReader(
            SimpleNamespace(
                get_attributes=lambda entity_id: {"forecast": "bad"}
            ),
            logger,
        )._read_forecast()
    with pytest.raises(RuntimeError, match="geen prijzen"):
        reader._read_forecast()

    assert reader._current_price() == 0.25
    reader.app.get_state = lambda entity_id: "invalid"
    assert reader._current_price() == 0.0
    reader.app.get_state = lambda entity_id: "-0.10"
    assert reader._current_price() == 0.0

    valid = {
        "start_date": base.isoformat(),
        "electricity_price": 2_500_000,
        "tariff_group": "normal",
        "sustainability_score": "75",
    }
    hour = reader._create_hour(valid)
    assert hour.price == pytest.approx(0.25)
    assert hour.price_raw == 2_500_000.0
    assert hour.tariff_group == "normal"
    assert hour.sustainability_score == 75.0

    datetime_record = dict(valid)
    datetime_record["start_date"] = base
    assert reader._create_hour(datetime_record).start == base
    with pytest.raises(TypeError, match="geen dictionary"):
        reader._create_hour([])
    with pytest.raises(ValueError, match="Ontbrekende start_date"):
        reader._create_hour({})
    with pytest.raises(TypeError, match="geen geldig type"):
        reader._create_hour({"start_date": 123})
    with pytest.raises(ValueError, match="Ongeldige start_date"):
        reader._create_hour({"start_date": "bad"})
    with pytest.raises(ValueError, match="niet timezone-aware"):
        reader._create_hour({"start_date": dt.datetime(2026, 9, 22, 10)})

    for price in (None, True, "bad", -1):
        with pytest.raises((ValueError, TypeError)):
            reader._create_hour(
                {"start_date": base.isoformat(), "electricity_price": price}
            )

    bad_score = dict(valid)
    bad_score["sustainability_score"] = "bad"
    assert reader._create_hour(bad_score).sustainability_score == 0.0
    bad_group = dict(valid)
    bad_group["tariff_group"] = 123
    assert reader._create_hour(bad_group).tariff_group == ""

    parsed = reader._parse_forecast([valid, {"start_date": "bad"}, "bad"])
    assert len(parsed) == 1

    duplicate = reader._create_hour(valid)
    later = reader._create_hour(
        {
            "start_date": (base + dt.timedelta(hours=2)).isoformat(),
            "electricity_price": 1_000_000,
        }
    )
    assert len(reader._remove_duplicates([duplicate, duplicate, later])) == 2
    old = reader._create_hour(
        {
            "start_date": (base - dt.timedelta(hours=3)).isoformat(),
            "electricity_price": 1_000_000,
        }
    )
    assert old not in reader._remove_past([old, duplicate])
    gap = reader._create_hour(
        {
            "start_date": (base + dt.timedelta(hours=3)).isoformat(),
            "electricity_price": 1_000_000,
        }
    )
    reader._validate_series([duplicate, gap])
    sorted_hours = reader._sort([later, duplicate])
    assert sorted_hours[0].start == duplicate.start
    assert [item.hour_index for item in sorted_hours] == [0, 1]

    reader.app.get_state = lambda entity_id: "0.30"
    data = reader._calculate_statistics([duplicate, later])
    assert data.current_price == pytest.approx(0.30)
    assert data.cheapest_price == pytest.approx(0.10)
    assert data.highest_price == pytest.approx(0.25)
    assert data.average_price == pytest.approx(0.175)
    assert data.valid_until == later.end
    reader.dump(data)
    assert reader._calculate_statistics([]).hours == []

    forecast = [
        valid,
        {
            "start_date": (base + dt.timedelta(hours=1)).isoformat(),
            "electricity_price": 1_000_000,
        },
    ]
    reader.app.get_attributes = lambda entity_id: {"forecast": forecast}
    result = reader.read()
    assert result.hours


async def test_form_defaults_are_used_and_options_merge_with_data(
    hass: HomeAssistant, valid_config: dict[str, str]
) -> None:
    """Test default entity selectors and options-over-data merging."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"]
    assert schema({}) == {
        CONF_ENTITY_PRICES: "sensor.zonneplan_current_electricity_tariff",
        CONF_ENTITY_SOLCAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
        CONF_ENTITY_SOLCAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
    }

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="EV Charge Planner",
        data=valid_config,
        options={CONF_ENTITY_PRICES: "sensor.option_prices"},
    )
    entry.add_to_hass(hass)
    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert options_result["type"] is FlowResultType.FORM
    assert options_result["data_schema"]({}) == {
        CONF_ENTITY_PRICES: "sensor.option_prices",
        CONF_ENTITY_SOLCAST_TODAY: valid_config[CONF_ENTITY_SOLCAST_TODAY],
        CONF_ENTITY_SOLCAST_TOMORROW: valid_config[CONF_ENTITY_SOLCAST_TOMORROW],
    }
