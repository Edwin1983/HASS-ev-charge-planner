"""Leesfouten in prijs- of Solcast-data mogen create_plan niet laten crashen."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from custom_components.ev_planner.core.ev_planner import EVPlannerController
from custom_components.ev_planner.core.logger import Logger


def _controller(prices, solcast):
    controller = object.__new__(EVPlannerController)
    controller.logger = Logger()
    controller.enabled = True
    controller._plan_lock = threading.RLock()
    controller.published = []
    controller._is_enabled = lambda: True
    controller._get_settings = lambda now: SimpleNamespace()
    controller._publish_planner_state = controller.published.append
    controller.prices = prices
    controller.solcast = solcast
    return controller


class _Raises:
    def read(self):
        raise RuntimeError("Forecast attribuut ontbreekt.")


class _Empty:
    def read(self):
        return SimpleNamespace(hours=[])


def test_missing_price_data_does_not_raise():
    controller = _controller(_Raises(), _Empty())

    assert controller.create_plan() is None
    assert controller.published == ["Geen prijsdata"]


def test_missing_solcast_data_does_not_raise():
    prices = SimpleNamespace(read=lambda: SimpleNamespace(hours=[object()]))
    controller = _controller(prices, _Raises())

    assert controller.create_plan() is None
    assert controller.published == ["Geen PV-data"]
