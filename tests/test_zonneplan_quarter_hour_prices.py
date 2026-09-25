"""Tests for Zonneplan quarter-hour price support."""

from datetime import datetime, timedelta

from custom_components.ev_planner.core.models import Hour
from custom_components.ev_planner.core.planner import EVPlanner
from custom_components.ev_planner.core.prices import PriceReader
from custom_components.ev_planner.core.solcast import SolcastReader


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
        start = datetime.fromisoformat(
            f"2026-09-25T16:{minute:02d}:00+02:00"
        )
        price_hour = Hour(
            start=start,
            end=start + timedelta(minutes=15),
            price=0.20,
            price_raw=2000000,
        )

        combined = planner._combine_hour_data(
            price_hour,
            solar_hour,
        )
        total += combined.pv_estimate

    assert abs(total - 4.0) < 0.000000001


class DummyApp:
    def __init__(self, attributes):
        self.attributes = attributes

    def get_attributes(self, entity_id):
        return self.attributes


def test_solcast_half_hour_record_has_correct_duration_and_energy():
    reader = SolcastReader(
        app=DummyApp({}),
        logger=DummyLogger(),
    )

    hour = reader._create_hour(
        {
            "period_start": "2026-09-25T16:00:00+02:00",
            "pv_estimate": 4.0,
            "pv_estimate10": 2.0,
            "pv_estimate90": 6.0,
        },
        interval_minutes=30,
    )

    assert hour.start == datetime.fromisoformat(
        "2026-09-25T16:00:00+02:00"
    )
    assert hour.end == datetime.fromisoformat(
        "2026-09-25T16:30:00+02:00"
    )
    assert abs(hour.pv_estimate - 2.0) < 0.000000001
    assert abs(hour.pv_estimate10 - 1.0) < 0.000000001
    assert abs(hour.pv_estimate90 - 3.0) < 0.000000001


def test_solcast_prefers_half_hourly_detailed_forecast():
    app = DummyApp(
        {
            "detailedForecast": [
                {
                    "period_start": "2026-09-25T16:00:00+02:00",
                    "pv_estimate": 4.0,
                }
            ],
            "detailedHourly": [
                {
                    "period_start": "2026-09-25T16:00:00+02:00",
                    "pv_estimate": 9.0,
                }
            ],
        }
    )

    reader = SolcastReader(
        app=app,
        logger=DummyLogger(),
    )

    forecast = reader._read_today()
    hours = reader._parse_forecast(forecast, reader._forecast_interval_minutes)

    assert reader._forecast_interval_minutes == 30
    assert len(hours) == 1
    assert hours[0].end == datetime.fromisoformat(
        "2026-09-25T16:30:00+02:00"
    )
    assert abs(hours[0].pv_estimate - 2.0) < 0.000000001


def test_solcast_falls_back_to_hourly_detailed_forecast():
    app = DummyApp(
        {
            "detailedHourly": [
                {
                    "period_start": "2026-09-25T16:00:00+02:00",
                    "pv_estimate": 4.0,
                }
            ]
        }
    )

    reader = SolcastReader(
        app=app,
        logger=DummyLogger(),
    )

    forecast = reader._read_today()
    hours = reader._parse_forecast(forecast, reader._forecast_interval_minutes)

    assert reader._forecast_interval_minutes == 60
    assert hours[0].end == datetime.fromisoformat(
        "2026-09-25T17:00:00+02:00"
    )
    assert abs(hours[0].pv_estimate - 4.0) < 0.000000001


def test_half_hour_solcast_is_split_correctly_over_zonneplan_quarters():
    planner = EVPlanner.__new__(EVPlanner)

    first_half_hour = Hour(
        start=datetime.fromisoformat("2026-09-25T16:00:00+02:00"),
        end=datetime.fromisoformat("2026-09-25T16:30:00+02:00"),
        pv_estimate=2.0,
    )
    second_half_hour = Hour(
        start=datetime.fromisoformat("2026-09-25T16:30:00+02:00"),
        end=datetime.fromisoformat("2026-09-25T17:00:00+02:00"),
        pv_estimate=1.0,
    )

    quarter_pv = []

    for minute in (0, 15, 30, 45):
        start = datetime.fromisoformat(
            f"2026-09-25T16:{minute:02d}:00+02:00"
        )
        price_hour = Hour(
            start=start,
            end=start + timedelta(minutes=15),
            price=0.20,
            price_raw=2000000,
        )

        if minute < 30:
            solar_hour = first_half_hour
        else:
            solar_hour = second_half_hour

        combined = planner._combine_hour_data(
            price_hour,
            solar_hour,
        )
        quarter_pv.append(combined.pv_estimate)

    assert quarter_pv == [1.0, 1.0, 0.5, 0.5]
    assert abs(sum(quarter_pv) - 3.0) < 0.000000001


def test_planner_selects_individual_zonneplan_quarters_by_price():
    from custom_components.ev_planner.core.logger import Logger
    from custom_components.ev_planner.core.models import PriceData, SolcastData
    from custom_components.ev_planner.core.planner import PlannerSettings

    start = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(hours=2)
    prices = [0.20, 0.40, 0.05, 0.30]
    price_hours = []
    solar_hours = []

    for index in range(4):
        quarter_start = start + timedelta(minutes=15 * index)
        quarter_end = quarter_start + timedelta(minutes=15)
        hour = Hour(
            start=quarter_start,
            end=quarter_end,
            price=prices[index],
            price_raw=prices[index] * 10000000,
        )
        hour.pv_estimate = 0.0
        hour.pv_estimate10 = 0.0
        hour.pv_estimate90 = 0.0
        price_hours.append(hour)

        solar_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                pv_estimate=0.0,
                pv_estimate10=0.0,
                pv_estimate90=0.0,
            )
        )

    planner = EVPlanner(
        PriceData(hours=price_hours),
        SolcastData(hours=solar_hours),
        PlannerSettings(
            energy_needed_kwh=1.38,
            departure_time=start + timedelta(hours=1),
            max_price=1.0,
            solar_is_free=True,
            max_charge_power_kw=3.68,
            max_phase_switches=8,
        ),
        Logger(),
    )

    plan = planner.create_plan()

    assert plan.complete
    assert len(plan.decisions) == 2
    assert [
        (decision.hour.start, decision.hour.end, decision.price)
        for decision in plan.decisions
    ] == [
        (
            start,
            start + timedelta(minutes=15),
            0.20,
        ),
        (
            start + timedelta(minutes=30),
            start + timedelta(minutes=45),
            0.05,
        ),
    ]
    assert abs(plan.energy_planned_kwh - 1.38) < 0.000001


def test_scheduler_treats_selected_quarters_as_separate_runtime_windows():
    from custom_components.ev_planner.core.logger import Logger
    from custom_components.ev_planner.core.models import PriceData, SolcastData
    from custom_components.ev_planner.core.planner import PlannerSettings
    from custom_components.ev_planner.core.scheduler import EVScheduler, SchedulerSettings

    start = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(hours=2)
    price_hours = []
    solar_hours = []

    for index in range(4):
        quarter_start = start + timedelta(minutes=15 * index)
        quarter_end = quarter_start + timedelta(minutes=15)
        price_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                price=[0.30, 0.40, 0.10, 0.20][index],
                price_raw=[0.30, 0.40, 0.10, 0.20][index] * 10000000,
            )
        )
        solar_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                pv_estimate=0.0,
                pv_estimate10=0.0,
                pv_estimate90=0.0,
            )
        )

    planner = EVPlanner(
        PriceData(hours=price_hours),
        SolcastData(hours=solar_hours),
        PlannerSettings(
            energy_needed_kwh=1.38,
            departure_time=start + timedelta(hours=1),
            max_price=1.0,
            solar_is_free=True,
            max_charge_power_kw=3.68,
            max_phase_switches=8,
        ),
        Logger(),
    )
    plan = planner.create_plan()

    scheduler = EVScheduler(
        SchedulerSettings(),
        Logger(),
    )
    scheduler.set_plan(plan)

    assert scheduler.get_current_hour(start) is None
    assert scheduler.get_current_hour(start + timedelta(minutes=15)) is None
    assert scheduler.get_current_hour(start + timedelta(minutes=30)) is not None
    assert scheduler.get_current_hour(start + timedelta(minutes=30)).start == (
        start + timedelta(minutes=30)
    )
    assert scheduler.get_current_hour(start + timedelta(minutes=45)) is not None
    assert scheduler.get_current_hour(start + timedelta(minutes=45)).start == (
        start + timedelta(minutes=45)
    )
    assert scheduler.get_current_hour(start + timedelta(hours=1)) is None
