"""
energy_feedback.py

Terugkoppeling tussen wat de planner dacht te laden en wat de lader
werkelijk heeft geleverd.

WAAROM
------
Een plan gaat uit van ideale vermogens. In werkelijkheid verliest een
fasewissel tijd, begrenst de auto soms de stroom, of start de lader later.
Zonder terugkoppeling haalt de auto het doel dan niet, ook al klopt het
plan op papier.

HOE
---
- `EnergyLedger` telt de werkelijk geleverde energie op uit een
  kWh-sensor van de lader. Dat kan een totaalteller zijn of een
  sessieteller die op 0 terugvalt; beide worden goed verwerkt.
- Een SESSIE heeft een doel (T0) en een beginstand. De nog te laden
  energie is altijd  T0 - (geleverd sinds begin sessie).
- TIJDENS een laadblok wordt de voortgang bewaakt: loopt de lader achter
  (of voor) op het plan, dan wordt direct bijgestuurd voor het resterende
  deel, zonder het einde van het blok af te wachten. Daardoor kan het blok
  doorlopen in plaats van dat de lader stopt en later moet herstarten.
- Na elk LAADBLOK (aaneengesloten geselecteerde beslissingen) wordt de
  cumulatief geplande energie vergeleken met de cumulatief geleverde
  energie sinds het plan werd gemaakt. Is het verschil groter dan de
  drempel, dan moet de planning opnieuw met de werkelijk resterende
  energie.

Deze module kent geen Home Assistant en doet zelf geen I/O.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

RESET_TOLERANCE_KWH = 0.05
MAX_STORED_SAMPLES = 2000
SAMPLE_INTERVAL_S = 60.0
PERSISTED_SAMPLES = 300


##############################################################################
# Planblokken
##############################################################################


@dataclass(frozen=True)
class PlanBlock:
    start: datetime
    end: datetime
    energy_kwh: float


def blocks_from_decisions(
    decisions: Iterable[tuple[datetime, datetime, float]],
    gap_s: float = 1.0,
) -> list[PlanBlock]:
    """Voeg aaneensluitende geselecteerde beslissingen samen tot blokken."""
    blocks: list[PlanBlock] = []
    for start, end, energy in sorted(decisions, key=lambda item: item[0]):
        if blocks and (start - blocks[-1].end).total_seconds() <= gap_s:
            previous = blocks[-1]
            blocks[-1] = PlanBlock(
                previous.start,
                max(previous.end, end),
                previous.energy_kwh + float(energy),
            )
        else:
            blocks.append(PlanBlock(start, end, float(energy)))
    return blocks


##############################################################################
# Energieboekhouding
##############################################################################


class EnergyLedger:
    """Telt werkelijk geleverde energie op uit een kWh-sensor."""

    def __init__(self) -> None:
        self.total = 0.0
        self._last_reading: float | None = None
        self._times: list[float] = []
        self._totals: list[float] = []
        self._pending: tuple[float, float] | None = None

    # ------------------------------------------------------------------

    def record(self, ts: datetime, reading: float | None) -> None:
        if reading is None:
            return
        try:
            reading = float(reading)
        except (TypeError, ValueError):
            return
        if math.isnan(reading) or math.isinf(reading) or reading < 0:
            return

        epoch = ts.timestamp()
        if self._times and epoch < self._times[-1]:
            epoch = self._times[-1]

        if self._last_reading is None:
            self._last_reading = reading
            self._store(epoch, self.total)
            return

        delta = reading - self._last_reading
        if delta < -RESET_TOLERANCE_KWH:
            # Sessieteller is teruggevallen naar ~0: alles sinds de reset
            # telt mee.
            delta = reading
        elif delta < 0:
            delta = 0.0
        self._last_reading = reading

        changed = delta > 0
        self.total += delta

        if changed:
            # Houd de stand van vlak VOOR de stijging vast, zodat een
            # lange stilstand niet uitgesmeerd wordt over de stijging.
            if self._pending is not None and (
                not self._times or self._pending[0] > self._times[-1]
            ):
                self._times.append(self._pending[0])
                self._totals.append(self._pending[1])
            self._store(epoch, self.total)
            self._pending = None
        else:
            self._pending = (epoch, self.total)
            if not self._times or epoch - self._times[-1] >= SAMPLE_INTERVAL_S:
                self._store(epoch, self.total)
                self._pending = None

    def _store(self, epoch: float, total: float) -> None:
        self._times.append(epoch)
        self._totals.append(total)
        if len(self._times) > MAX_STORED_SAMPLES:
            del self._times[: len(self._times) - MAX_STORED_SAMPLES]
            del self._totals[: len(self._totals) - MAX_STORED_SAMPLES]

    # ------------------------------------------------------------------

    @property
    def last_sample_time(self) -> float | None:
        candidates = []
        if self._times:
            candidates.append(self._times[-1])
        if self._pending is not None:
            candidates.append(self._pending[0])
        return max(candidates) if candidates else None

    def total_at(self, ts: datetime) -> float:
        """Cumulatieve geleverde energie op een moment (lineair geinterpoleerd)."""
        epoch = ts.timestamp()
        times = list(self._times)
        totals = list(self._totals)
        if self._pending is not None and (not times or self._pending[0] > times[-1]):
            times.append(self._pending[0])
            totals.append(self._pending[1])
        if not times:
            return self.total
        if epoch <= times[0]:
            return totals[0]
        if epoch >= times[-1]:
            return totals[-1]
        index = bisect.bisect_right(times, epoch)
        t0, t1 = times[index - 1], times[index]
        v0, v1 = totals[index - 1], totals[index]
        if t1 == t0:
            return v1
        return v0 + (v1 - v0) * (epoch - t0) / (t1 - t0)

    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "last_reading": self._last_reading,
            "times": self._times[-PERSISTED_SAMPLES:],
            "totals": self._totals[-PERSISTED_SAMPLES:],
            "pending": list(self._pending) if self._pending else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnergyLedger":
        ledger = cls()
        try:
            ledger.total = float(data.get("total", 0.0))
            last = data.get("last_reading")
            ledger._last_reading = None if last is None else float(last)
            ledger._times = [float(v) for v in data.get("times", [])]
            ledger._totals = [float(v) for v in data.get("totals", [])]
            pending = data.get("pending")
            ledger._pending = (
                (float(pending[0]), float(pending[1])) if pending else None
            )
        except (TypeError, ValueError, IndexError):
            return cls()
        if len(ledger._times) != len(ledger._totals):
            return cls()
        return ledger


##############################################################################
# Terugkoppeling
##############################################################################


@dataclass(frozen=True)
class FeedbackConfig:
    # Vanaf dit verschil (kWh) tussen gepland en geleverd wordt herpland.
    threshold_kwh: float = 0.3
    # Wachttijd na het einde van een blok voordat het wordt beoordeeld.
    settle_s: float = 45.0
    # Maximaal wachten op een meting na het blok.
    max_wait_for_sample_s: float = 600.0
    # Niet vaker herplannen dan dit.
    min_replan_interval_s: float = 300.0
    # Onder deze restenergie geldt het doel als bereikt.
    done_tolerance_kwh: float = 0.05
    # Voortgangscontrole tijdens een blok: grotere drempel (de kWh-sensor
    # loopt een beetje achter), een korte aanloop na de start van het blok
    # en het afwijkende resultaat moet bij opeenvolgende controles blijven.
    progress_threshold_kwh: float = 0.5
    progress_grace_s: float = 180.0
    progress_confirmations: int = 2


@dataclass(frozen=True)
class Evaluation:
    block_end: datetime
    planned_kwh: float
    actual_kwh: float
    deviation_kwh: float  # werkelijk - gepland (negatief = minder geladen)
    replan: bool
    skipped_reason: str | None = None


@dataclass(frozen=True)
class Progress:
    """Voortgang binnen een lopend laadblok."""

    at: datetime
    planned_kwh: float
    actual_kwh: float
    deviation_kwh: float  # werkelijk - gepland (negatief = achter op schema)
    replan: bool


class EnergyFeedback:
    """Sessie, planblokken en beoordeling per laadblok."""

    def __init__(
        self,
        ledger: EnergyLedger | None = None,
        config: FeedbackConfig | None = None,
    ) -> None:
        self.ledger = ledger or EnergyLedger()
        self.config = config or FeedbackConfig()

        self.target_kwh: float | None = None
        self.baseline_total = 0.0
        self.session_started_at: float | None = None

        self._plan_baseline_total: float | None = None
        self._blocks: list[PlanBlock] = []
        self._decisions: list[PlanBlock] = []
        self._evaluated: list[bool] = []
        self._last_disconnect_at: float | None = None
        self._progress_hits = 0
        self.last_progress: Progress | None = None

        self.replans = 0
        self._last_replan_at: float | None = None
        self.last_evaluation: Evaluation | None = None

    # ------------------------------------------------------------------
    # Sessie
    # ------------------------------------------------------------------

    @property
    def session_active(self) -> bool:
        return self.target_kwh is not None

    def start_session(self, target_kwh: float, now: datetime) -> None:
        self.target_kwh = float(target_kwh)
        self.baseline_total = self.ledger.total
        self.session_started_at = now.timestamp()
        self._plan_baseline_total = None
        self._blocks = []
        self._decisions = []
        self._evaluated = []
        self._progress_hits = 0
        self.last_progress = None
        self.replans = 0
        self._last_replan_at = None
        self.last_evaluation = None

    def end_session(self) -> None:
        self.target_kwh = None
        self._plan_baseline_total = None
        self._blocks = []
        self._decisions = []
        self._evaluated = []
        self._progress_hits = 0
        self.last_progress = None
        self.last_evaluation = None

    def delivered_kwh(self) -> float:
        if self.target_kwh is None:
            return 0.0
        return max(0.0, self.ledger.total - self.baseline_total)

    def remaining_kwh(self) -> float | None:
        if self.target_kwh is None:
            return None
        return max(0.0, self.target_kwh - self.delivered_kwh())

    def is_done(self) -> bool:
        remaining = self.remaining_kwh()
        return remaining is not None and remaining <= self.config.done_tolerance_kwh

    # ------------------------------------------------------------------
    # Plan en verbinding
    # ------------------------------------------------------------------

    def on_new_plan(
        self,
        plan_time: datetime,
        decisions: Iterable[tuple[datetime, datetime, float]],
    ) -> None:
        """Leg vast wat het (nieuwe) plan van nu af aan verwacht te leveren."""
        self._plan_baseline_total = self.ledger.total_at(plan_time)
        decisions = list(decisions)
        self._decisions = [
            PlanBlock(start, end, float(energy))
            for start, end, energy in sorted(decisions, key=lambda item: item[0])
            if end > plan_time
        ]
        self._blocks = [
            block
            for block in blocks_from_decisions(decisions)
            if block.end > plan_time
        ]
        self._evaluated = [False] * len(self._blocks)
        self._progress_hits = 0

    def note_connection(self, connected: bool, now: datetime) -> None:
        if not connected:
            self._last_disconnect_at = now.timestamp()

    # ------------------------------------------------------------------
    # Beoordeling
    # ------------------------------------------------------------------

    def evaluate(self, now: datetime) -> list[Evaluation]:
        """Beoordeel alle afgelopen, nog niet beoordeelde laadblokken."""
        results: list[Evaluation] = []
        if self._plan_baseline_total is None:
            return results

        now_epoch = now.timestamp()
        cfg = self.config

        for index, block in enumerate(self._blocks):
            if self._evaluated[index]:
                continue

            end_epoch = block.end.timestamp()
            if now_epoch < end_epoch + cfg.settle_s:
                break  # blokken zijn chronologisch

            last_sample = self.ledger.last_sample_time
            if (
                (last_sample is None or last_sample < end_epoch)
                and now_epoch < end_epoch + cfg.max_wait_for_sample_s
            ):
                break  # nog geen meting van na het blok

            planned = sum(
                other.energy_kwh
                for other in self._blocks
                if other.end <= block.end
            )
            actual = self.ledger.total_at(block.end) - self._plan_baseline_total
            deviation = actual - planned

            self._evaluated[index] = True

            if (
                self._last_disconnect_at is not None
                and self._last_disconnect_at > block.start.timestamp()
            ):
                evaluation = Evaluation(
                    block.end,
                    planned,
                    actual,
                    deviation,
                    False,
                    "auto was tijdens het blok losgekoppeld",
                )
            else:
                evaluation = Evaluation(
                    block.end,
                    planned,
                    actual,
                    deviation,
                    abs(deviation) >= cfg.threshold_kwh,
                )

            results.append(evaluation)
            self.last_evaluation = evaluation

        return results

    def check_progress(self, now: datetime) -> Progress | None:
        """Loopt de lader tijdens een laadblok nog op schema?

        De geplande energie wordt per beslissing evenredig met de tijd
        opgeteld en vergeleken met wat de lader sinds het plan leverde, op
        het moment van de laatste meting (zodat een trage sensor niet als
        achterstand telt). Geeft None als er niets te beoordelen valt.
        """
        if self._plan_baseline_total is None or not self._decisions:
            return None

        cfg = self.config
        now_epoch = now.timestamp()

        # Alleen binnen een lopend (aaneengesloten) blok, na de aanloop.
        block = next(
            (
                b
                for b in self._blocks
                if b.start.timestamp() <= now_epoch < b.end.timestamp()
            ),
            None,
        )
        if block is None:
            self._progress_hits = 0
            return None
        if now_epoch - block.start.timestamp() < cfg.progress_grace_s:
            return None

        if (
            self._last_disconnect_at is not None
            and self._last_disconnect_at > block.start.timestamp()
        ):
            return None

        last_sample = self.ledger.last_sample_time
        if last_sample is None:
            return None
        ref_epoch = min(now_epoch, last_sample)
        ref = datetime.fromtimestamp(ref_epoch, tz=now.tzinfo)

        planned = 0.0
        for item in self._decisions:
            start = item.start.timestamp()
            end = item.end.timestamp()
            if end <= start:
                continue
            fraction = (ref_epoch - start) / (end - start)
            planned += item.energy_kwh * min(1.0, max(0.0, fraction))

        actual = self.ledger.total_at(ref) - self._plan_baseline_total
        deviation = actual - planned

        if abs(deviation) >= cfg.progress_threshold_kwh:
            self._progress_hits += 1
        else:
            self._progress_hits = 0

        progress = Progress(
            now,
            planned,
            actual,
            deviation,
            self._progress_hits >= cfg.progress_confirmations,
        )
        self.last_progress = progress
        return progress

    def should_replan_progress(self, progress: Progress | None, now: datetime) -> bool:
        if progress is None or not progress.replan:
            return False
        if (
            self._last_replan_at is not None
            and (
                now.timestamp() - self._last_replan_at
                < self.config.min_replan_interval_s
            )
        ):
            return False
        return True

    def should_replan(self, evaluations: list[Evaluation], now: datetime) -> bool:
        """Alleen de meest recente beoordeling telt, met een rem op herplannen."""
        relevant = [e for e in evaluations if e.skipped_reason is None]
        if not relevant or not relevant[-1].replan:
            return False
        if (
            self._last_replan_at is not None
            and (
                now.timestamp() - self._last_replan_at
                < self.config.min_replan_interval_s
            )
        ):
            return False
        return True

    def mark_replanned(self, now: datetime) -> None:
        self.replans += 1
        self._last_replan_at = now.timestamp()

    # ------------------------------------------------------------------
    # Samenvatting en opslag
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        last = self.last_evaluation
        return {
            "session_active": self.session_active,
            "target_kwh": self.target_kwh,
            "delivered_kwh": round(self.delivered_kwh(), 3),
            "remaining_kwh": (
                None if self.remaining_kwh() is None else round(self.remaining_kwh(), 3)
            ),
            "replans": self.replans,
            "last_block_end": last.block_end.isoformat() if last else None,
            "last_planned_kwh": round(last.planned_kwh, 3) if last else None,
            "last_actual_kwh": round(last.actual_kwh, 3) if last else None,
            "last_deviation_kwh": round(last.deviation_kwh, 3) if last else None,
            "last_skipped_reason": last.skipped_reason if last else None,
            "progress_deviation_kwh": (
                (
                    round(self.last_progress.deviation_kwh, 3)
                    if self.last_progress
                    else None
                )
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger": self.ledger.to_dict(),
            "target_kwh": self.target_kwh,
            "baseline_total": self.baseline_total,
            "session_started_at": self.session_started_at,
            "plan_baseline_total": self._plan_baseline_total,
            "blocks": [
                [b.start.isoformat(), b.end.isoformat(), b.energy_kwh]
                for b in self._blocks
            ],
            "decisions": [
                [b.start.isoformat(), b.end.isoformat(), b.energy_kwh]
                for b in self._decisions
            ],
            "evaluated": list(self._evaluated),
            "last_disconnect_at": self._last_disconnect_at,
            "replans": self.replans,
            "last_replan_at": self._last_replan_at,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        config: FeedbackConfig | None = None,
    ) -> "EnergyFeedback":
        feedback = cls(config=config)
        if not data:
            return feedback
        try:
            feedback.ledger = EnergyLedger.from_dict(data.get("ledger", {}))
            target = data.get("target_kwh")
            feedback.target_kwh = None if target is None else float(target)
            feedback.baseline_total = float(data.get("baseline_total", 0.0))
            feedback.session_started_at = data.get("session_started_at")
            base = data.get("plan_baseline_total")
            feedback._plan_baseline_total = None if base is None else float(base)
            feedback._blocks = [
                PlanBlock(
                    datetime.fromisoformat(item[0]),
                    datetime.fromisoformat(item[1]),
                    float(item[2]),
                )
                for item in data.get("blocks", [])
            ]
            feedback._decisions = [
                PlanBlock(
                    datetime.fromisoformat(item[0]),
                    datetime.fromisoformat(item[1]),
                    float(item[2]),
                )
                for item in data.get("decisions", [])
            ]
            feedback._evaluated = [bool(v) for v in data.get("evaluated", [])]
            if len(feedback._evaluated) != len(feedback._blocks):
                feedback._evaluated = [False] * len(feedback._blocks)
            feedback._last_disconnect_at = data.get("last_disconnect_at")
            feedback.replans = int(data.get("replans", 0))
            feedback._last_replan_at = data.get("last_replan_at")
        except (TypeError, ValueError, IndexError):
            return cls(config=config)
        return feedback
