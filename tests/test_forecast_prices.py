"""Voorspelde prijzen vullen ontbrekende (morgen-)prijzen aan."""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.ev_planner.core.logger import Logger
from custom_components.ev_planner.core.models import SolcastData
from custom_components.ev_planner.core.planner import EVPlanner, PlannerSettings
from custom_components.ev_planner.core.prices import PriceReader


def _base() -> datetime:
    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    return now - timedelta(minutes=now.minute % 15) + timedelta(minutes=15)


class FakeApp:
    def __init__(self, final, forecast, state="0.2"):
        self.final = final
        self.forecast = forecast
        self.state = state

    def get_attributes(self, entity_id):
        if entity_id == "sensor.final":
            return {"forecast": self.final}
        if entity_id == "sensor.fc":
            if isinstance(self.forecast, Exception):
                raise self.forecast
            if isinstance(self.forecast, dict):
                return self.forecast
            return {"raw_today": None, "raw_forecast": self.forecast}
        return {}

    def get_state(self, entity_id):
        return self.state


def _final(start, count, price=0.30):
    return [
        {
            "start_date": (start + timedelta(minutes=15 * i)).isoformat(),
            "end_date": (start + timedelta(minutes=15 * (i + 1))).isoformat(),
            "price_tax_included": {"amount": int(price * 10_000_000)},
        }
        for i in range(count)
    ]


def _forecast(start, count, price=0.10, z=False):
    out = []
    for i in range(count):
        a = start + timedelta(minutes=15 * i)
        b = a + timedelta(minutes=15)
        if z:
            from datetime import timezone

            a = a.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            b = b.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({"start": a, "end": b, "value": price})
    return out


def _reader(final, forecast, entity="sensor.fc"):
    app = FakeApp(final, forecast)
    return PriceReader(app, Logger(), "sensor.final", entity)


def test_forecast_continues_after_final_prices_and_is_flagged():
    base = _base()
    final = _final(base, 8)
    # De voorspelling begint eerder en overlapt: alleen het staartstuk telt.
    forecast = _forecast(base, 40, 0.05, z=True)
    reader = _reader(final, forecast)

    data = reader.extend_with_forecast(reader.read())

    assert len(data.hours) == 40
    assert data.valid_until == base + timedelta(minutes=15 * 8)
    assert data.forecast_until == base + timedelta(minutes=15 * 40)
    assert data.forecast_hours == 32
    assert [h.is_forecast for h in data.hours] == [False] * 8 + [True] * 32
    # definitieve prijzen onaangetast
    assert all(abs(h.price - 0.30) < 1e-9 for h in data.hours[:8])
    assert all(abs(h.price - 0.07) < 1e-9 for h in data.hours[8:])
    # aaneensluitend en doorlopend genummerd
    for a, b in zip(data.hours, data.hours[1:]):
        assert a.end == b.start
        assert b.hour_index == a.hour_index + 1


def test_disabled_without_entity():
    base = _base()
    reader = _reader(_final(base, 8), _forecast(base, 40), entity="")
    data = reader.extend_with_forecast(reader.read())
    assert len(data.hours) == 8 and data.forecast_hours == 0


def test_forecast_errors_and_junk_leave_final_prices_alone():
    base = _base()
    final = _final(base, 8)

    for forecast in (RuntimeError("weg"), None, [], "tekst", [{"x": 1}, 5, None]):
        reader = _reader(final, forecast)
        data = reader.extend_with_forecast(reader.read())
        assert len(data.hours) == 8 and data.forecast_hours == 0


def test_junk_records_are_skipped_but_valid_ones_used():
    base = _base()
    forecast = _forecast(base, 20)
    forecast[10]["value"] = "x"  # breekt de reeks na slot 9
    forecast.insert(0, {"start": "kapot", "end": "kapot", "value": 1})
    reader = _reader(_final(base, 8), forecast)

    data = reader.extend_with_forecast(reader.read())

    # slots 8 en 9 sluiten aan; slot 10 ontbreekt dus stopt de reeks daar
    assert data.forecast_hours == 2


def test_forecast_that_does_not_connect_is_ignored():
    base = _base()
    forecast = _forecast(base + timedelta(minutes=15 * 12), 20)
    reader = _reader(_final(base, 8), forecast)
    data = reader.extend_with_forecast(reader.read())
    assert data.forecast_hours == 0


def test_negative_forecast_prices_are_kept():
    base = _base()
    reader = _reader(_final(base, 4), _forecast(base, 12, -0.10))
    data = reader.extend_with_forecast(reader.read())
    assert abs(data.hours[-1].price - (-0.08)) < 1e-9 and data.hours[-1].is_forecast


def test_planner_uses_forecast_hours_and_marks_them():
    base = _base()
    # Definitieve prijzen duur, voorspelde uren goedkoop.
    reader = _reader(_final(base, 8, 0.40), _forecast(base, 48, 0.05))
    prices = reader.extend_with_forecast(reader.read())

    settings = PlannerSettings(
        energy_needed_kwh=11.0,
        departure_time=base + timedelta(minutes=15 * 47),
        max_price=1.0,
        max_phase_switches=8,
    )
    plan = EVPlanner(prices, SolcastData(), settings, Logger()).create_plan()

    chosen = [d for d in plan.decisions if d.selected]
    assert chosen
    assert all(d.hour.is_forecast for d in chosen)
    assert abs(sum(d.energy_kwh for d in chosen) - 11.0) < 1e-3


def test_markup_applies_only_to_forecast_and_is_configurable():
    base = _base()
    app = FakeApp(_final(base, 4, 0.30), _forecast(base, 12, 0.10))
    reader = PriceReader(
        app,
        Logger(),
        "sensor.final",
        "sensor.fc",
        forecast_markup=0.05,
    )
    data = reader.extend_with_forecast(reader.read())
    assert abs(data.hours[0].price - 0.30) < 1e-9
    assert abs(data.hours[-1].price - 0.15) < 1e-9


def test_broken_final_sensor_falls_back_to_forecast_only():
    base = _base()
    reader = _reader(None, _forecast(base, 40, 0.05))  # geen 'forecast'-attribuut

    data, final_until = reader.read_with_forecast()

    assert len(data.hours) == 40
    assert all(h.is_forecast for h in data.hours)
    assert all(abs(h.price - 0.07) < 1e-9 for h in data.hours)
    assert data.forecast_hours == 40
    # "definitieve prijzen" eindigen nu: zodra de echte prijzen terug zijn,
    # wordt het plan ververst
    assert final_until is not None and final_until <= datetime.now().astimezone()
    assert [h.hour_index for h in data.hours] == list(range(40))


def test_no_fallback_without_forecast_sensor_or_data():
    base = _base()
    for reader in (
        _reader(None, _forecast(base, 40), entity=""),
        _reader(None, []),
        _reader(None, RuntimeError("weg")),
    ):
        try:
            reader.read_with_forecast()
        except Exception:
            pass
        else:
            raise AssertionError("fout verwacht")


def test_read_with_forecast_unchanged_when_final_prices_work():
    base = _base()
    reader = _reader(_final(base, 8), _forecast(base, 40, 0.05))
    data, final_until = reader.read_with_forecast()
    assert len(data.hours) == 40
    assert final_until == base + timedelta(minutes=15 * 8)
    assert [h.is_forecast for h in data.hours] == [False] * 8 + [True] * 32


def test_fallback_combines_raw_today_and_raw_forecast_without_duplicates():
    base = _base()
    today = _forecast(base - timedelta(minutes=60), 20, 0.10)   # deels verleden
    later = _forecast(base + timedelta(minutes=15 * 16), 30, 0.20)
    overlap = _forecast(base + timedelta(minutes=15 * 16), 5, 0.99)
    reader = _reader(
        None,
        {"raw_today": today, "raw_forecast": later + overlap},
    )

    data, _ = reader.read_with_forecast()

    starts = [h.start for h in data.hours]
    assert starts == sorted(set(starts))
    assert data.hours[0].end > datetime.now().astimezone()
    assert len(data.hours) == 17 + 30  # incl. het lopende kwartier
    for a, b in zip(data.hours, data.hours[1:]):
        assert a.end == b.start
