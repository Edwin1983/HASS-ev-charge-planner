"""Tests for Zonneplan quarter-hour price support."""

from datetime import datetime

from custom_components.ev_planner.core.models import Hour
from custom_components.ev_planner.core.planner import EVPlanner
from custom_components.ev_planner.core.prices import PriceReader


class DummyLogger:
    def debug(self, message):
        return None

    def warning(self, message):
        return None


def _reader():
    return PriceReader(
        app=None,
        logger=DummyLogger(),
        entity_id="sensor.zonneplan_current_quarter_hourly_electricity_tariff",
    )


def test_create_hour_supports_zonneplan_quarter_hour_record():
    reader = _reader()

    hour = reader._create_hour(
        {
            "start_date": "2026-09-25T15:00:00+02:00",
            "end_date": "2026-09-25T15:15:00+02:00",
            "price_tax_included": {
                "amount": 1823819,
            },
            "sustainability_score": {
                "permille": 1000,
            },
        }
    )

    assert hour.start == datetime.fromisoformat(
        "2026-09-25T15:00:00+02:00"
    )
    assert hour.end == datetime.fromisoformat(
        "2026-09-25T15:15:00+02:00"
    )
    assert abs(hour.price - 0.1823819) < 0.000000001
    assert hour.price_raw == 1823819.0
    assert hour.sustainability_score == 1000.0


def test_create_hour_keeps_legacy_zonneplan_hour_format():
    reader = _reader()

    hour = reader._create_hour(
        {
            "start_date": "2026-09-25T15:00:00+02:00",
            "electricity_price": 1823819,
        }
    )

    assert hour.end == datetime.fromisoformat(
        "2026-09-25T16:00:00+02:00"
    )
    assert abs(hour.price - 0.1823819) < 0.000000001


def test_create_hour_rejects_invalid_quarter_end():
    reader = _reader()

    try:
        reader._create_hour(
            {
                "start_date": "2026-09-25T15:15:00+02:00",
                "end_date": "2026-09-25T15:00:00+02:00",
                "price_tax_included": {
                    "amount": 1823819,
                },
            }
        )
    except ValueError as err:
        assert str(err) == "end_date moet na start_date liggen."
    else:
        raise AssertionError("Expected ValueError")


def test_combine_hour_data_scales_hourly_solcast_to_quarter():
    planner = EVPlanner.__new__(EVPlanner)

    price_hour = Hour(
        start=datetime.fromisoformat("2026-09-25T16:00:00+02:00"),
        end=datetime.fromisoformat("2026-09-25T16:15:00+02:00"),
        price=0.20,
        price_raw=2000000,
    )

    solar_hour = Hour(
        start=datetime.fromisoformat("2026-09-25T16:00:00+02:00"),
        end=datetime.fromisoformat("2026-09-25T17:00:00+02:00"),
        pv_estimate=4.0,
        pv_estimate10=2.0,
        pv_estimate90=6.0,
    )

    combined = planner._combine_hour_data(
        price_hour,
        solar_hour,
    )

    assert abs(combined.pv_estimate - 1.0) < 0.000000001
    assert abs(combined.pv_estimate10 - 0.5) < 0.000000001
    assert abs(combined.pv_estimate90 - 1.5) < 0.000000001


def test_four_quarters_preserve_total_hourly_solcast_energy():
    planner = EVPlanner.__new__(EVPlanner)

    solar_hour = Hour(
        start=datetime.fromisoformat("2026-09-25T16:00:00+02:00"),
        end=datetime.fromisoformat("2026-09-25T17:00:00+02:00"),
        pv_estimate=4.0,
    )

    total = 0.0

    for minute in (0, 15, 30, 45):
        price_hour = Hour(
            start=datetime.fromisoformat(
                f"2026-09-25T16:{minute:02d}:00+02:00"
            ),
            end=datetime.fromisoformat(
                f"2026-09-25T16:{minute + 15:02d}:00+02:00"
            ),
            price=0.20,
            price_raw=2000000,
        )

        combined = planner._combine_hour_data(
            price_hour,
            solar_hour,
        )
        total += combined.pv_estimate

    assert abs(total - 4.0) < 0.000000001
