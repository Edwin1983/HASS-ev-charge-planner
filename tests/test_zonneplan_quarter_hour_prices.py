"""Tests for Zonneplan quarter-hour electricity prices."""

from datetime import datetime, timedelta

from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import Hour, PriceData, SolcastData
from custom_components.ev_planner.core.prices import PriceReader
from custom_components.ev_planner.core.solcast import SolcastReader


def test_zonneplan_quarter_hour_format_is_parsed():
    attributes = {
        "forecast": [
            {
                "start_date": "2026-09-25T16:00:00+02:00",
                "end_date": "2026-09-25T16:15:00+02:00",
                "price_tax_included": {"amount": 2000000},
            }
        ]
    }
    reader = PriceReader(DummyApp(attributes), Logger(), "sensor.zonneplan")
    hours = reader._parse_forecast(attributes["forecast"])
    assert len(hours) == 1
    assert hours[0].start.hour == 16
    assert hours[0].start.minute == 0
    assert hours[0].end.minute == 15
    assert hours[0].price == 0.2


def test_legacy_hourly_format_is_still_parsed():
    attributes = {
        "forecast": [
            {
                "start_date": "2026-09-25T16:00:00+02:00",
                "electricity_price": 2000000,
                "sustainability_score": {"permille": 900},
            }
        ]
    }
    reader = PriceReader(None, "sensor.zonneplan", attributes=attributes)
    hours = reader.read()
    assert len(hours) == 1
    assert hours[0].end.minute == 0
    assert hours[0].end.hour == 17
    assert hours[0].price == 0.2
    assert hours[0].sustainability_score == 0.9


def test_invalid_quarter_hour_end_date_is_rejected():
    attributes = {
        "forecast": [
            {
                "start_date": "2026-09-25T16:00:00+02:00",
                "end_date": "2026-09-25T15:45:00+02:00",
                "price_tax_included": {"amount": 2000000},
            }
        ]
    }
    reader = PriceReader(None, "sensor.zonneplan", attributes=attributes)
    assert reader.read() == []


def test_solcast_half_hour_record_has_correct_duration_and_energy():
    app = DummyApp(
        {
            "detailedForecast": [
                {
                    "period_start": "2026-09-25T16:00:00+02:00",
                    "pv_estimate": 4.0,
                    "pv_estimate10": 2.0,
                    "pv_estimate90": 6.0,
                }
            ]
        }
    )
    reader = SolcastReader(app, Logger(), "sensor.solcast", "sensor.solcast_tomorrow")
    hours = reader._parse_forecast(attributes, 30)
    assert len(hours) == 1
    assert hours[0].end.hour == 16
    assert hours[0].end.minute == 30
    assert hours[0].pv_estimate == 2.0
    assert hours[0].pv_estimate10 == 1.0
    assert hours[0].pv_estimate90 == 3.0


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
                    "pv_estimate": 8.0,
                }
            ],
        }
    )
    reader = SolcastReader(app, Logger(), "sensor.solcast", "sensor.solcast_tomorrow")
    hours = reader._parse_forecast(attributes, 60)
    assert len(hours) == 1
    assert hours[0].end.minute == 30
    assert hours[0].pv_estimate == 2.0


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
    reader = SolcastReader(app, "sensor.solcast", "sensor.solcast_tomorrow")
    hours = reader.read()
    assert len(hours) == 1
    assert hours[0].end.hour == 17
    assert hours[0].end.minute == 0
    assert hours[0].pv_estimate == 4.0


def test_half_hour_solcast_is_split_correctly_over_zonneplan_quarters():
    from custom_components.ev_planner.core.planner import EVPlanner

    price_hours = [
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:00:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T16:15:00+02:00"
            ),
            price=0.10,
        ),
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:15:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T16:30:00+02:00"
            ),
            price=0.20,
        ),
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:30:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T16:45:00+02:00"
            ),
            price=0.30,
        ),
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:45:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T17:00:00+02:00"
            ),
            price=0.40,
        ),
    ]
    solar_hours = [
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:00:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T16:30:00+02:00"
            ),
            pv_estimate=2.0,
            pv_estimate10=1.0,
            pv_estimate90=3.0,
        ),
        Hour(
            start=datetime.fromisoformat(
                "2026-09-25T16:30:00+02:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-25T17:00:00+02:00"
            ),
            pv_estimate=1.0,
            pv_estimate10=0.5,
            pv_estimate90=1.5,
        ),
    ]

    planner = EVPlanner(
        PriceData(),
        SolcastData(),
        PlannerSettings(
            energy_needed_kwh=1.0,
            departure_time=datetime.fromisoformat("2026-09-25T17:00:00+02:00"),
            max_price=1.0,
        ),
        Logger(),
    )
    combined = planner._combine_hour_data(price_hours, solar_hours)

    assert [round(hour.pv_estimate, 6) for hour in combined] == [
        1.0,
        1.0,
        0.5,
        0.5,
    ]
    assert sum(hour.pv_estimate for hour in combined) == 3.0


def test_planner_selects_individual_zonneplan_quarters_by_price():
    from custom_components.ev_planner.core.planner import (
        EVPlanner,
        PlannerSettings,
    )

    start = (
        datetime.now()
        .astimezone()
        .replace(second=0, microsecond=0)
        + timedelta(hours=2)
    )
    price_hours = []
    solar_hours = []

    prices = [0.20, 0.40, 0.05, 0.30]
    for index, price in enumerate(prices):
        quarter_start = start + timedelta(minutes=15 * index)
        quarter_end = quarter_start + timedelta(minutes=15)
        price_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                price=price,
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

    settings = PlannerSettings(
        energy_needed_kwh=1.38,
        departure_time=start + timedelta(hours=1),
        max_price=1.0,
        solar_is_free=False,
        max_charge_power_kw=3.68,
        max_phase_switches=8,
    )
    planner = EVPlanner(
        PriceData(hours=price_hours),
        SolcastData(hours=solar_hours),
        settings,
        Logger(),
    )
    plan = planner.create_plan()

    selected = [decision for decision in plan.decisions if decision.selected]
    assert len(selected) == 2
    assert [decision.hour.start for decision in selected] == [
        start,
        start + timedelta(minutes=30),
    ]
    assert abs(plan.energy_planned_kwh - 1.38) < 0.000001


def test_scheduler_treats_selected_quarters_as_separate_runtime_windows():
    from custom_components.ev_planner.core.planner import PlannerSettings
    from custom_components.ev_planner.core.scheduler import (
        EVScheduler,
        SchedulerSettings,
    )

    start = (
        datetime.now()
        .astimezone()
        .replace(second=0, microsecond=0)
        + timedelta(hours=2)
    )
    price_hours = []
    solar_hours = []

    prices = [0.30, 0.40, 0.10, 0.20]
    for index, price in enumerate(prices):
        quarter_start = start + timedelta(minutes=15 * index)
        quarter_end = quarter_start + timedelta(minutes=15)
        price_hours.append(
            Hour(
                start=quarter_start,
                end=quarter_end,
                price=price,
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

    settings = PlannerSettings(
        energy_needed_kwh=1.38,
        departure_time=start + timedelta(hours=1),
        max_price=1.0,
        solar_is_free=False,
        max_charge_power_kw=3.68,
        max_phase_switches=8,
    )
    planner = EVPlanner(
        PriceData(hours=price_hours),
        SolcastData(hours=solar_hours),
        settings,
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

    current = scheduler.get_current_hour(start + timedelta(minutes=30))
    assert current is not None
    assert current.start == start + timedelta(minutes=30)

    current = scheduler.get_current_hour(start + timedelta(minutes=45))
    assert current is not None
    assert current.start == start + timedelta(minutes=45)

    assert scheduler.get_current_hour(start + timedelta(hours=1)) is None


class DummyApp:
    def __init__(self, attributes):
        self.attributes = attributes

    def get_attributes(self, entity_id):
        return self.attributes
