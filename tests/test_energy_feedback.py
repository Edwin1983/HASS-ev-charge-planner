"""Tests voor energieboekhouding en terugkoppeling per laadblok."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.ev_planner.core.energy_feedback import (
    EnergyFeedback,
    EnergyLedger,
    FeedbackConfig,
    blocks_from_decisions,
)

T0 = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


##############################################################################
# Ledger
##############################################################################


def test_ledger_counts_increments_of_a_total_meter():
    ledger = EnergyLedger()
    for minute, reading in [(0, 1000.0), (10, 1000.0), (20, 1002.5), (30, 1005.0)]:
        ledger.record(at(minute), reading)
    assert abs(ledger.total - 5.0) < 1e-9


def test_ledger_handles_session_meter_reset():
    ledger = EnergyLedger()
    for minute, reading in [(0, 8.0), (10, 10.0), (20, 0.0), (30, 3.0)]:
        ledger.record(at(minute), reading)
    # 8 -> 10 (+2), reset naar 0 (+0), daarna 3 (+3)
    assert abs(ledger.total - 5.0) < 1e-9


def test_ledger_ignores_invalid_readings():
    ledger = EnergyLedger()
    ledger.record(at(0), 5.0)
    for bad in (None, "x", float("nan"), -1.0):
        ledger.record(at(1), bad)
    ledger.record(at(2), 6.0)
    assert abs(ledger.total - 1.0) < 1e-9


def test_long_standstill_is_not_smeared_over_a_later_rise():
    ledger = EnergyLedger()
    ledger.record(at(0), 0.0)
    ledger.record(at(30), 0.0)
    ledger.record(at(60), 0.0)
    ledger.record(at(180), 0.0)  # 3 uur stilstand
    ledger.record(at(185), 1.0)  # dan pas een stijging

    # Op minuut 120 mag nog niets geleverd zijn.
    assert ledger.total_at(at(120)) == 0.0
    assert ledger.total_at(at(182.5)) > 0.4


def test_interpolation_between_samples():
    ledger = EnergyLedger()
    ledger.record(at(0), 0.0)
    ledger.record(at(10), 2.0)
    assert abs(ledger.total_at(at(5)) - 1.0) < 1e-9


def test_ledger_roundtrip():
    ledger = EnergyLedger()
    for minute, reading in [(0, 0.0), (10, 1.0), (20, 3.0)]:
        ledger.record(at(minute), reading)
    again = EnergyLedger.from_dict(ledger.to_dict())
    assert abs(again.total - ledger.total) < 1e-9
    assert abs(again.total_at(at(15)) - ledger.total_at(at(15))) < 1e-9


##############################################################################
# Blokken
##############################################################################


def test_contiguous_decisions_form_one_block():
    blocks = blocks_from_decisions(
        [
            (at(0), at(15), 2.0),
            (at(15), at(30), 2.0),
            (at(90), at(105), 2.5),
        ]
    )
    assert len(blocks) == 2
    assert blocks[0].energy_kwh == 4.0 and blocks[0].end == at(30)
    assert blocks[1].start == at(90)


##############################################################################
# Terugkoppeling
##############################################################################

CFG = FeedbackConfig(threshold_kwh=0.3, settle_s=60, min_replan_interval_s=300)


def _setup(delivered_by_end_of_block_1):
    """Plan: blok 1 (0-60 min, 5 kWh) en blok 2 (120-180 min, 5 kWh)."""
    feedback = EnergyFeedback(config=CFG)
    feedback.ledger.record(at(-1), 100.0)
    feedback.start_session(10.0, at(-1))
    feedback.on_new_plan(
        at(0),
        [(at(0), at(60), 5.0), (at(120), at(180), 5.0)],
    )
    feedback.ledger.record(at(0), 100.0)
    feedback.ledger.record(at(60), 100.0 + delivered_by_end_of_block_1)
    return feedback


def test_block_on_target_does_not_trigger_replan():
    feedback = _setup(5.0)
    evals = feedback.evaluate(at(62))
    assert len(evals) == 1
    assert abs(evals[0].deviation_kwh) < 1e-9
    assert not feedback.should_replan(evals, at(62))


def test_shortfall_triggers_replan_with_correct_deviation():
    feedback = _setup(4.2)
    evals = feedback.evaluate(at(62))
    assert abs(evals[0].deviation_kwh - (-0.8)) < 1e-9
    assert evals[0].replan
    assert feedback.should_replan(evals, at(62))
    assert abs(feedback.remaining_kwh() - 5.8) < 1e-9


def test_overdelivery_also_triggers_replan():
    feedback = _setup(5.6)
    evals = feedback.evaluate(at(62))
    assert evals[0].deviation_kwh > 0.3 and evals[0].replan


def test_small_deviation_is_tolerated():
    feedback = _setup(4.9)
    evals = feedback.evaluate(at(62))
    assert not evals[0].replan


def test_block_is_not_evaluated_before_settle_time_or_a_later_sample():
    feedback = EnergyFeedback(config=CFG)
    feedback.ledger.record(at(-1), 0.0)
    feedback.start_session(5.0, at(-1))
    feedback.on_new_plan(at(0), [(at(0), at(60), 5.0)])
    feedback.ledger.record(at(30), 2.0)  # laatste meting is van VOOR het einde

    assert feedback.evaluate(at(61)) == []  # binnen settle-tijd
    assert feedback.evaluate(at(65)) == []  # nog geen meting van na het blok
    feedback.ledger.record(at(66), 4.0)
    assert len(feedback.evaluate(at(67))) == 1


def test_block_is_evaluated_after_max_wait_even_without_new_sample():
    feedback = EnergyFeedback(
        config=FeedbackConfig(settle_s=60, max_wait_for_sample_s=600)
    )
    feedback.ledger.record(at(-1), 0.0)
    feedback.start_session(5.0, at(-1))
    feedback.on_new_plan(at(0), [(at(0), at(60), 5.0)])
    feedback.ledger.record(at(30), 2.0)
    assert len(feedback.evaluate(at(75))) == 1


def test_deviation_is_cumulative_over_blocks():
    feedback = _setup(4.9)  # blok 1: -0.1 (binnen tolerantie)
    feedback.evaluate(at(62))
    feedback.ledger.record(at(120), 104.9)
    feedback.ledger.record(at(180), 104.9 + 4.7)  # blok 2: -0.3 extra
    evals = feedback.evaluate(at(182))
    assert abs(evals[0].deviation_kwh - (-0.4)) < 1e-9  # cumulatief
    assert evals[0].replan


def test_disconnected_car_is_not_a_deviation():
    feedback = _setup(0.0)
    feedback.note_connection(False, at(20))
    evals = feedback.evaluate(at(62))
    assert evals[0].skipped_reason is not None
    assert not feedback.should_replan(evals, at(62))


def test_replans_are_rate_limited():
    feedback = _setup(4.0)
    evals = feedback.evaluate(at(62))
    assert feedback.should_replan(evals, at(62))
    feedback.mark_replanned(at(62))
    assert not feedback.should_replan(evals, at(63))
    assert feedback.should_replan(evals, at(70))
    assert feedback.replans == 1


def test_session_remaining_and_done():
    feedback = EnergyFeedback(config=CFG)
    feedback.ledger.record(at(0), 50.0)
    feedback.start_session(8.0, at(0))
    feedback.ledger.record(at(30), 53.0)
    assert abs(feedback.remaining_kwh() - 5.0) < 1e-9
    assert not feedback.is_done()
    feedback.ledger.record(at(60), 58.0)
    assert feedback.is_done()
    assert feedback.remaining_kwh() == 0.0


def test_new_plan_rebaselines_the_comparison():
    feedback = _setup(4.0)
    feedback.evaluate(at(62))
    feedback.mark_replanned(at(62))
    feedback.on_new_plan(at(62), [(at(120), at(180), 6.0)])
    feedback.ledger.record(at(120), 104.0)
    feedback.ledger.record(at(180), 110.0)
    evals = feedback.evaluate(at(182))
    assert abs(evals[0].deviation_kwh) < 1e-9  # t.o.v. het NIEUWE plan


def test_feedback_roundtrip_keeps_session_and_progress():
    feedback = _setup(4.2)
    feedback.evaluate(at(62))
    again = EnergyFeedback.from_dict(feedback.to_dict(), CFG)
    assert again.session_active
    assert abs(again.remaining_kwh() - feedback.remaining_kwh()) < 1e-9
    assert again.evaluate(at(63)) == []  # blok 1 al beoordeeld


##############################################################################
# Voortgang tijdens een blok
##############################################################################


def _feedback_with_block(threshold=0.5, grace=180.0, confirmations=2):
    fb = EnergyFeedback(
        config=FeedbackConfig(
            progress_threshold_kwh=threshold,
            progress_grace_s=grace,
            progress_confirmations=confirmations,
            min_replan_interval_s=300,
        )
    )
    fb.ledger.record(at(0), 100.0)
    fb.start_session(10.0, at(0))
    # 60 minuten, 6 kWh gepland
    fb.on_new_plan(at(0), [(at(0), at(60), 6.0)])
    return fb


def _feed(fb, minute, reading):
    fb.ledger.record(at(minute), reading)


def test_progress_on_schedule_does_nothing():
    fb = _feedback_with_block()
    for minute in (10, 20, 30):
        _feed(fb, minute, 100.0 + 0.1 * minute)  # 6 kW = 0,1 kWh/min
        progress = fb.check_progress(at(minute))
        assert progress is not None and not progress.replan
        assert abs(progress.deviation_kwh) < 0.01


def test_progress_behind_schedule_needs_confirmation_then_replans():
    fb = _feedback_with_block()
    _feed(fb, 20, 100.0 + 0.5 * 20 / 10)  # slechts 1 kWh na 20 min i.p.v. 2
    first = fb.check_progress(at(20))
    assert first.deviation_kwh < -0.5 and not first.replan  # eerste keer: nog niet

    _feed(fb, 21, 101.05)
    second = fb.check_progress(at(21))
    assert second.replan
    assert fb.should_replan_progress(second, at(21))

    fb.mark_replanned(at(21))
    assert not fb.should_replan_progress(second, at(23))  # rem op herplannen


def test_progress_has_a_start_up_grace_period():
    fb = _feedback_with_block()
    assert fb.check_progress(at(2)) is None  # lader begint net
    _feed(fb, 4, 100.0)
    assert fb.check_progress(at(4)) is not None


def test_progress_ignores_time_outside_a_planned_block():
    fb = _feedback_with_block()
    _feed(fb, 90, 100.0)
    assert fb.check_progress(at(90)) is None


def test_progress_ahead_of_schedule_also_counts():
    fb = _feedback_with_block()
    _feed(fb, 20, 100.0 + 3.0)  # 3 kWh na 20 min i.p.v. 2
    fb.check_progress(at(20))
    _feed(fb, 21, 103.2)
    assert fb.check_progress(at(21)).replan


def test_slow_sensor_is_not_counted_as_a_shortfall():
    fb = _feedback_with_block()
    _feed(fb, 10, 101.0)
    # De sensor is sinds minuut 10 niet gewijzigd en er is geen nieuwe meting:
    # vergelijk op het moment van de laatste meting, niet op "nu".
    progress = fb.check_progress(at(12))
    assert progress is not None
    assert abs(progress.deviation_kwh) < 0.05


def test_progress_state_survives_a_restart():
    fb = _feedback_with_block()
    restored = EnergyFeedback.from_dict(fb.to_dict(), fb.config)
    restored.ledger.record(at(20), 100.0)
    restored.ledger.record(at(21), 100.0)
    assert restored.check_progress(at(21)) is not None
