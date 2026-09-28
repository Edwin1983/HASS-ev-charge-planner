"""
Experimental quarter-hour EV optimizer.

This module is intentionally separate from the production planner.

Model:
- one decision slot per quarter-hour;
- 1F/3F and integer 6..16 A;
- PV is free energy;
- grid energy is charged at the slot price;
- max_price is a HARD price-per-kWh limit for grid energy;
- phase changes are part of the DP state;
- dominated states are removed from the Pareto frontier;
- the final slot may be partial so the target can be met exactly.

The optimizer does not control Home Assistant or a charger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import inf

from .config import (
    MAX_CURRENT_1PH,
    MAX_POWER_1PH,
    MAX_POWER_3PH,
    MIN_CURRENT,
    VOLTAGE,
)


ENERGY_TICK_KWH = 0.0025
ENERGY_TICKS_PER_KWH = 400


@dataclass(frozen=True)
class QuarterHourAction:
    index: int
    start: datetime
    end: datetime
    phases: int
    current_a: int
    energy_kwh: float
    free_energy_kwh: float
    paid_energy_kwh: float
    cost: float


@dataclass
class QuarterHourPlan:
    actions: list[QuarterHourAction]
    energy_kwh: float
    free_energy_kwh: float
    paid_energy_kwh: float
    cost: float
    complete: bool
    missing_energy_kwh: float
    phase_switches: int


@dataclass
class _Node:
    cost: float
    energy_ticks: int
    phase: int
    switches: int
    parent: "_Node | None"
    action: QuarterHourAction | None


class QuarterHourOptimizer:
    """Find a minimum-cost quarter-hour EV charging plan."""

    def __init__(
        self,
        energy_needed_kwh: float,
        max_price: float,
        max_charge_power_kw: float = MAX_POWER_3PH,
        max_phase_switches: int = 2,
    ) -> None:
        self.target_kwh = float(energy_needed_kwh)
        self.max_price = float(max_price)
        self.max_power_kw = float(max_charge_power_kw)
        self.max_phase_switches = max(0, min(int(max_phase_switches), 8))

        if self.target_kwh <= 0:
            raise ValueError("energy_needed_kwh moet groter dan 0 zijn.")

        if self.max_price < 0:
            raise ValueError("max_price mag niet negatief zijn.")

        if self.max_power_kw <= 0:
            raise ValueError("max_charge_power_kw moet groter dan 0 zijn.")

        if self.max_power_kw > MAX_POWER_3PH:
            raise ValueError(
                f"max_charge_power_kw mag niet groter zijn dan "
                f"{MAX_POWER_3PH:.2f} kW."
            )

    def optimize(self, slots) -> QuarterHourPlan:
        """Optimize the supplied chronological quarter-hour slots."""
        ordered = list(slots)
        ordered.sort(key=lambda slot: slot.start)

        if not ordered:
            return self._empty_plan()

        target_ticks = self._to_ticks(self.target_kwh)
        suffix_capacity = self._build_suffix_capacity(ordered)

        # State is (phase, switches). Each state contains only its
        # non-dominated energy/cost frontier.
        frontiers = {(0, 0): {0: _Node(0.0, 0, 0, 0, None, None)}}

        best_terminal: _Node | None = None

        for index, slot in enumerate(ordered):
            next_frontiers = {}

            duration_hours = self._duration_hours(slot)
            if duration_hours <= 0:
                frontiers = self._prune_frontiers(next_frontiers)
                continue

            for state, nodes in frontiers.items():
                phase_before, switches_before = state

                for node in nodes.values():
                    remaining_ticks = target_ticks - node.energy_ticks
                    if remaining_ticks <= 0:
                        if self._better_terminal(node, best_terminal):
                            best_terminal = node
                        continue

                    if (
                        node.energy_ticks + suffix_capacity[index]
                        < target_ticks
                    ):
                        continue

                    # Charging is optional in every quarter-hour. Carry the
                    # current node forward unchanged so the optimizer can
                    # leave expensive/ineligible slots unused. A skipped slot
                    # does not create or count a phase switch.
                    if (
                        node.energy_ticks + suffix_capacity[index + 1]
                        >= target_ticks
                    ):
                        skip_bucket = next_frontiers.setdefault(
                            (phase_before, switches_before),
                            {},
                        )
                        self._keep_frontier(skip_bucket, node)

                    for phase in (1, 3):
                        switches = self._switch_count(
                            phase_before,
                            phase,
                            node.switches,
                        )
                        if switches > self.max_phase_switches:
                            continue

                        # For a grid-eligible slot, maximum current dominates
                        # lower currents. Above max_price, however, a lower
                        # current can be fully PV-covered while maximum current
                        # would mix PV and grid. Keep the highest PV-safe current
                        # as the second non-terminal candidate.
                        currents = [self._max_current(phase)]
                        if float(slot.price) > self.max_price:
                            pv_rate = self._pv_rate(slot)
                            pv_current = int(
                                pv_rate * 1000.0 / (VOLTAGE * phase)
                            )
                            pv_current = min(self._max_current(phase), pv_current)
                            if pv_current >= MIN_CURRENT and pv_current not in currents:
                                currents.append(pv_current)

                        for current in currents:
                            action_power = self._power_kw(phase, current)
                            amount = action_power * duration_hours
                            amount_ticks = self._to_ticks(amount)

                            if amount_ticks <= 0 or amount_ticks > remaining_ticks:
                                continue

                            free = min(
                                self._pv_rate(slot),
                                action_power,
                            ) * duration_hours
                            paid = max(0.0, amount - free)

                            if (
                                float(slot.price) > self.max_price
                                and paid > 0.000001
                            ):
                                continue

                            action = QuarterHourAction(
                                index=index,
                                start=slot.start,
                                end=slot.end,
                                phases=phase,
                                current_a=current,
                                energy_kwh=amount,
                                free_energy_kwh=free,
                                paid_energy_kwh=paid,
                                cost=paid * float(slot.price),
                            )
                            candidate = _Node(
                                cost=node.cost + paid * float(slot.price),
                                energy_ticks=node.energy_ticks + amount_ticks,
                                phase=phase,
                                switches=switches,
                                parent=node,
                                action=action,
                            )

                            bucket = next_frontiers.setdefault(
                                (phase, switches),
                                {},
                            )
                            self._keep_frontier(bucket, candidate)

                        # Terminal partial action. This is the only action
                        # that may be shorter than the quarter-hour.
                        duration = duration_hours
                        max_power = self._power_kw(
                            phase,
                            self._max_current(phase),
                        )
                        required_kwh = (
                            float(remaining_ticks) / ENERGY_TICKS_PER_KWH
                        )
                        required_power = required_kwh / duration

                        if required_power <= max_power + 0.000001:
                            current = self._current_for_power(
                                phase,
                                required_power,
                            )
                            if current <= self._max_current(phase):
                                actual_power = self._power_kw(phase, current)
                                finish_duration = required_kwh / actual_power

                                if finish_duration <= duration + 0.000001:
                                    free = min(
                                        self._pv_rate(slot),
                                        actual_power,
                                    ) * finish_duration
                                    paid = max(0.0, required_kwh - free)

                                    if (
                                        float(slot.price) <= self.max_price
                                        or paid <= 0.000001
                                    ):
                                        action = QuarterHourAction(
                                            index=index,
                                            start=slot.start,
                                            end=slot.start
                                            + self._seconds_to_timedelta(
                                                finish_duration * 3600.0
                                            ),
                                            phases=phase,
                                            current_a=current,
                                            energy_kwh=required_kwh,
                                            free_energy_kwh=free,
                                            paid_energy_kwh=paid,
                                            cost=paid * float(slot.price),
                                        )
                                        candidate = _Node(
                                            cost=node.cost
                                            + action.cost,
                                            energy_ticks=target_ticks,
                                            phase=phase,
                                            switches=switches,
                                            parent=node,
                                            action=action,
                                        )
                                        if self._better_terminal(
                                            candidate,
                                            best_terminal,
                                        ):
                                            best_terminal = candidate

            frontiers = self._prune_frontiers(next_frontiers)

            if best_terminal is not None:
                # We can still find a cheaper terminal later, so do not stop
                # merely because a complete plan exists.
                pass

            if not frontiers and best_terminal is not None:
                break

        if best_terminal is None:
            return self._best_partial_plan(frontiers)

        return self._build_plan(best_terminal)

    def _build_suffix_capacity(self, slots) -> list[int]:
        suffix = [0] * (len(slots) + 1)
        for index in range(len(slots) - 1, -1, -1):
            slot = slots[index]
            max_power = self._power_kw(3, self._max_current(3))
            duration = self._duration_hours(slot)
            raw = max_power * duration

            if float(slot.price) > self.max_price:
                raw = min(raw, self._pv_rate(slot) * duration)

            suffix[index] = suffix[index + 1] + self._to_ticks(raw)
        return suffix

    def _keep_frontier(self, bucket, candidate: _Node) -> None:
        old = bucket.get(candidate.energy_ticks)
        if old is None or candidate.cost < old.cost - 0.000000001:
            bucket[candidate.energy_ticks] = candidate

    def _prune_frontiers(self, frontiers):
        result = {}
        for state, bucket in frontiers.items():
            items = sorted(
                bucket.values(),
                key=lambda node: (node.energy_ticks, node.cost),
                reverse=True,
            )
            kept = {}
            best_cost = inf
            for node in items:
                if node.cost < best_cost - 0.000000001:
                    kept[node.energy_ticks] = node
                    best_cost = node.cost
            if kept:
                result[state] = kept
        return result

    def _better_terminal(self, candidate, current) -> bool:
        if current is None:
            return True
        if candidate.cost < current.cost - 0.000000001:
            return True
        if abs(candidate.cost - current.cost) <= 0.000000001:
            return candidate.switches < current.switches
        return False

    def _best_partial_plan(self, frontiers) -> QuarterHourPlan:
        best = None
        for bucket in frontiers.values():
            for node in bucket.values():
                if best is None:
                    best = node
                elif node.energy_ticks > best.energy_ticks:
                    best = node
                elif (
                    node.energy_ticks == best.energy_ticks
                    and node.cost < best.cost
                ):
                    best = node

        if best is None:
            return self._empty_plan()

        actions = self._actions_from_node(best)
        energy = sum(action.energy_kwh for action in actions)
        free = sum(action.free_energy_kwh for action in actions)
        paid = sum(action.paid_energy_kwh for action in actions)
        return QuarterHourPlan(
            actions=actions,
            energy_kwh=energy,
            free_energy_kwh=free,
            paid_energy_kwh=paid,
            cost=sum(action.cost for action in actions),
            complete=False,
            missing_energy_kwh=max(0.0, self.target_kwh - energy),
            phase_switches=best.switches,
        )

    def _build_plan(self, node: _Node) -> QuarterHourPlan:
        actions = self._actions_from_node(node)
        return QuarterHourPlan(
            actions=actions,
            energy_kwh=sum(action.energy_kwh for action in actions),
            free_energy_kwh=sum(action.free_energy_kwh for action in actions),
            paid_energy_kwh=sum(action.paid_energy_kwh for action in actions),
            cost=sum(action.cost for action in actions),
            complete=True,
            missing_energy_kwh=0.0,
            phase_switches=node.switches,
        )

    def _actions_from_node(self, node: _Node) -> list[QuarterHourAction]:
        actions = []
        current = node
        while current is not None and current.action is not None:
            actions.append(current.action)
            current = current.parent
        actions.reverse()
        return actions

    def _empty_plan(self) -> QuarterHourPlan:
        return QuarterHourPlan(
            actions=[],
            energy_kwh=0.0,
            free_energy_kwh=0.0,
            paid_energy_kwh=0.0,
            cost=0.0,
            complete=False,
            missing_energy_kwh=self.target_kwh,
            phase_switches=0,
        )

    @staticmethod
    def _duration_hours(slot) -> float:
        return max(0.0, (slot.end - slot.start).total_seconds() / 3600.0)

    @staticmethod
    def _pv_rate(slot) -> float:
        duration = QuarterHourOptimizer._duration_hours(slot)
        if duration <= 0:
            return 0.0
        return max(0.0, float(getattr(slot, "usable_pv", 0.0)) / duration)

    @staticmethod
    def _power_kw(phases: int, current: int) -> float:
        return VOLTAGE * current * phases / 1000.0

    def _max_current(self, phases: int) -> int:
        max_power = MAX_POWER_1PH if phases == 1 else min(
            MAX_POWER_3PH,
            self.max_power_kw,
        )
        if phases == 1:
            max_power = min(MAX_POWER_1PH, self.max_power_kw)
        current = int(max_power * 1000.0 / (VOLTAGE * phases))
        return min(MAX_CURRENT_1PH, max(current, MIN_CURRENT))

    @staticmethod
    def _current_for_power(phases: int, required_power: float) -> int:
        current = int(
            (required_power * 1000.0) / (VOLTAGE * phases)
        )
        if QuarterHourOptimizer._power_kw(phases, current) + 0.000001 < required_power:
            current += 1
        return max(MIN_CURRENT, current)

    @staticmethod
    def _switch_count(previous: int, current: int, switches: int) -> int:
        if previous == 0 or previous == current:
            return switches
        return switches + 1

    @staticmethod
    def _to_ticks(kwh: float) -> int:
        return max(0, int(round(float(kwh) * ENERGY_TICKS_PER_KWH)))

    @staticmethod
    def _seconds_to_timedelta(seconds: float):
        from datetime import timedelta
        return timedelta(seconds=seconds)
