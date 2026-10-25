"""Een plan dat is gemaakt zonder de prijzen van morgen wordt vernieuwd."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.ev_planner.core.ev_planner import EVPlannerController
from custom_components.ev_planner.core.logger import Logger

NOW = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
DEPARTURE = NOW + timedelta(hours=11)  # morgen 07:00


def _controller(prices_until, plan_until, departure=DEPARTURE):
    controller = object.__new__(EVPlannerController)
    controller.logger = Logger()
    controller._plan_lock = threading.RLock()
    controller.enabled = True
    controller.last_plan = object()
    controller.last_plan_time = NOW
    controller.plan_prices_until = plan_until
    controller.plan_departure = departure
    controller.prices = SimpleNamespace(
        read=lambda: SimpleNamespace(valid_until=prices_until)
    )
    controller.replans = []
    controller.replan_keep_previous = lambda now=None: controller.replans.append(now)
    controller.scheduler = SimpleNamespace(update=lambda now: False)
    controller._get_current_decision = lambda now: None
    controller._publish_planner_decision = lambda decision: None
    controller.status = SimpleNamespace(
        update=lambda now: None,
        log_status=lambda: None,
    )
    controller._publish_charging_allowed = lambda: None
    controller._is_enabled = lambda: True
    return controller


def test_coverage_is_none_without_a_plan_and_false_when_prices_end_early():
    controller = _controller(NOW, None)
    assert controller.prices_cover_departure() is None
    controller.plan_prices_until = NOW + timedelta(hours=4)
    assert controller.prices_cover_departure() is False
    controller.plan_prices_until = DEPARTURE
    assert controller.prices_cover_departure() is True


def test_new_prices_after_an_incomplete_horizon_trigger_a_replan():
    plan_until = NOW + timedelta(hours=4)
    controller = _controller(NOW + timedelta(hours=28), plan_until)

    controller.update(NOW)

    assert controller.replans == [NOW]


def test_no_replan_while_prices_have_not_grown():
    plan_until = NOW + timedelta(hours=4)
    controller = _controller(plan_until, plan_until)

    controller.update(NOW)

    assert controller.replans == []


def test_no_replan_when_the_plan_already_covered_the_departure():
    controller = _controller(NOW + timedelta(hours=40), DEPARTURE)

    controller.update(NOW)

    assert controller.replans == []


def test_unreadable_prices_do_not_replan_or_raise():
    controller = _controller(None, NOW + timedelta(hours=4))

    def boom():
        raise RuntimeError("geen forecast")

    controller.prices = SimpleNamespace(read=boom)
    controller.update(NOW)

    assert controller.replans == []


def test_price_check_is_throttled():
    plan_until = NOW + timedelta(hours=4)
    controller = _controller(plan_until, plan_until)

    controller.update(NOW)
    controller.prices = SimpleNamespace(
        read=lambda: SimpleNamespace(valid_until=NOW + timedelta(hours=28))
    )
    controller.update(NOW + timedelta(minutes=1))
    assert controller.replans == []

    controller.update(NOW + timedelta(minutes=6))
    assert len(controller.replans) == 1
