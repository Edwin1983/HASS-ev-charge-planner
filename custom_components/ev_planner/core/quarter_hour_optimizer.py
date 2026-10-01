"""
Experimental quarter-hour EV optimizer.

This module is intentionally separate from the production planner.

The DP uses dense numeric arrays for costs and compact byte back-pointers.
The state model remains identical to the reference optimizer:
- one decision slot per quarter-hour;
- 1F/3F and integer 6..16 A;
- PV is free energy;
- grid energy is charged at the slot price;
- max_price is a HARD price-per-kWh limit for grid energy;
- phase changes are part of the DP state;
- the final slot may be partial so the target can be met exactly.

The optimizer does not control Home Assistant or a charger.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import resource
import tracemalloc

from .config import (
    MAX_CURRENT_1PH,
    MAX_POWER_1PH,
    MAX_POWER_3PH,
    MIN_CURRENT,
    VOLTAGE,
)


ENERGY_TICK_KWH = 0.0025
ENERGY_TICKS_PER_KWH = 400
_EPSILON = 0.000000001
_UNREACHABLE = float("inf")

# State 0 is the initial "no phase yet" state. The remaining states are
# phase 1 / phase 3 combined with the number of phase switches used.
_PHASES = (0, 1, 3)
_PHASE_INDEX = {phase: index for index, phase in enumerate(_PHASES)}
_STATE_COUNT = 3 * 9
_SKIP_ACTION = 1
_ACTION_BASE = 2


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


@dataclass(frozen=True)
class _Action:
    phases: int
    current_a: int
    energy_kwh: float
    energy_ticks: int
    free_energy_kwh: float
    paid_energy_kwh: float
    cost: float
    duration_hours: float


@dataclass(frozen=True)
class _Terminal:
    cost: float
    slot_index: int
    state: int
    energy_ticks: int
    phases: int
    current_a: int
    energy_kwh: float
    free_energy_kwh: float
    paid_energy_kwh: float
    finish_duration_hours: float
    switches: int


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
        self.memory_profile = []
        self._memory_profile_enabled = (
            os.environ.get("EV_PLANNER_MEMORY_PROFILE") == "1"
        )
        self._profile_nodes_created = 0

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

        if self._memory_profile_enabled:
            tracemalloc.start()

        if not ordered:
            return self._empty_plan()

        target_ticks = self._to_ticks(self.target_kwh)
        width = target_ticks + 1
        suffix_capacity = self._build_suffix_capacity(ordered)
        slot_actions = self._build_slot_actions(ordered)

        costs = array("d", [_UNREACHABLE]) * (_STATE_COUNT * width)
        costs[0] = 0.0
        active = [[] for _ in range(_STATE_COUNT)]
        active[0].append(0)

        # One compact byte per state/energy/slot. 0 means unreachable,
        # 1 means "skip this slot", and >=2 encodes phase + current.
        parent_layers: list[bytearray] = []
        best_terminal: _Terminal | None = None

        for index, slot in enumerate(ordered):
            next_costs = array(
                "d",
                [_UNREACHABLE],
            ) * (_STATE_COUNT * width)
            next_active = [[] for _ in range(_STATE_COUNT)]
            parent = bytearray(_STATE_COUNT * width)
            actions = slot_actions[index]

            duration_hours = self._duration_hours(slot)
            if duration_hours <= 0:
                parent_layers.append(parent)
                costs = next_costs
                active = next_active
                self._record_array_profile(
                    index,
                    active,
                    best_terminal,
                    width,
                )
                continue

            price = float(slot.price)
            pv_rate = self._pv_rate(slot)
            negative_price = price < 0
            price_above_limit = price > self.max_price
            max_switches = self.max_phase_switches
            epsilon = _EPSILON
            unreachable = _UNREACHABLE
            state_phases = (0, 1, 3, 1, 3, 1, 3, 1, 3,
                            1, 3, 1, 3, 1, 3, 1, 3, 1)
            state_switches = (0, 0, 0, 1, 1, 2, 2, 3, 3,
                              4, 4, 5, 5, 6, 6, 7, 7, 8)
            max_current_1 = self._max_current(1)
            max_current_3 = self._max_current(3)
            voltage = VOLTAGE

            for state in range(_STATE_COUNT):
                energy_list = active[state]
                if not energy_list:
                    continue

                phase_before = state_phases[state]
                switches_before = state_switches[state]

                for energy_ticks in energy_list:
                    base_index = state * width + energy_ticks
                    base_cost = costs[base_index]
                    if base_cost == unreachable:
                        continue

                    remaining_ticks = target_ticks - energy_ticks
                    if remaining_ticks <= 0:
                        terminal = _Terminal(
                            cost=base_cost,
                            slot_index=index,
                            state=state,
                            energy_ticks=energy_ticks,
                            phases=phase_before,
                            current_a=0,
                            energy_kwh=0.0,
                            free_energy_kwh=0.0,
                            paid_energy_kwh=0.0,
                            finish_duration_hours=0.0,
                            switches=switches_before,
                        )
                        if best_terminal is None or (
                            terminal.cost < best_terminal.cost - epsilon
                            or (
                                abs(terminal.cost - best_terminal.cost) <= epsilon
                                and terminal.switches < best_terminal.switches
                            )
                        ):
                            best_terminal = terminal
                        continue

                    required_kwh = remaining_ticks / ENERGY_TICKS_PER_KWH
                    required_power = required_kwh / duration_hours

                    # Evaluate the partial final charge. All quantities that
                    # do not depend on the state are kept local to this slot.
                    for phase, phase_index, max_current in (
                        (1, 1, max_current_1),
                        (3, 2, max_current_3),
                    ):
                        switched = phase_before != 0 and phase_before != phase
                        switches = switches_before + int(switched)
                        if switches > max_switches:
                            continue

                        if negative_price:
                            current = max_current
                        else:
                            current = int(
                                (required_power * 1000.0)
                                / (voltage * phase)
                            )
                            if (
                                voltage * current * phase / 1000.0
                                + 0.000001
                                < required_power
                            ):
                                current += 1
                            if current < MIN_CURRENT:
                                current = MIN_CURRENT

                        if current > max_current:
                            continue

                        actual_power = voltage * current * phase / 1000.0
                        finish_duration = required_kwh / actual_power
                        if finish_duration > duration_hours + 0.000001:
                            continue

                        free = min(pv_rate, actual_power) * finish_duration
                        paid = required_kwh - free
                        if paid < 0.0:
                            paid = 0.0
                        if price_above_limit and paid > 0.000001:
                            continue

                        terminal = _Terminal(
                            cost=base_cost + paid * price,
                            slot_index=index,
                            state=state,
                            energy_ticks=energy_ticks,
                            phases=phase,
                            current_a=current,
                            energy_kwh=required_kwh,
                            free_energy_kwh=free,
                            paid_energy_kwh=paid,
                            finish_duration_hours=finish_duration,
                            switches=switches,
                        )
                        if best_terminal is None or (
                            terminal.cost < best_terminal.cost - epsilon
                            or (
                                abs(terminal.cost - best_terminal.cost) <= epsilon
                                and terminal.switches < best_terminal.switches
                            )
                        ):
                            best_terminal = terminal

                    if energy_ticks + suffix_capacity[index] < target_ticks:
                        continue

                    if energy_ticks + suffix_capacity[index + 1] >= target_ticks:
                        next_index = base_index
                        old = next_costs[next_index]
                        if old == unreachable:
                            next_active[state].append(energy_ticks)
                            next_costs[next_index] = base_cost
                            parent[next_index] = _SKIP_ACTION
                        elif base_cost < old - epsilon:
                            next_costs[next_index] = base_cost
                            parent[next_index] = _SKIP_ACTION

                    for action in actions:
                        phase = action.phases
                        switched = phase_before != 0 and phase_before != phase
                        switches = switches_before + int(switched)
                        if switches > max_switches:
                            continue
                        action_ticks = action.energy_ticks
                        if action_ticks > remaining_ticks:
                            continue

                        candidate_energy = energy_ticks + action_ticks
                        candidate_cost = base_cost + action.cost
                        candidate_state = (
                            switches * 3 + (1 if phase == 1 else 2)
                        )
                        action_code = (
                            _ACTION_BASE
                            + (0 if phase == 1 else 11)
                            + (action.current_a - MIN_CURRENT)
                            + (22 if switched else 0)
                        )
                        next_index = candidate_state * width + candidate_energy
                        old = next_costs[next_index]
                        if old != unreachable and candidate_cost >= old - epsilon:
                            continue
                        if old == unreachable:
                            next_active[candidate_state].append(candidate_energy)
                        next_costs[next_index] = candidate_cost
                        parent[next_index] = action_code
                        if self._memory_profile_enabled:
                            self._profile_nodes_created += 1

            parent_layers.append(parent)
            costs = next_costs
            active = next_active

            self._record_array_profile(
                index,
                active,
                best_terminal,
                width,
            )

            if not active and best_terminal is not None:
                break

        if self._memory_profile_enabled:
            self._record_array_final_profile(
                len(parent_layers),
                active,
                best_terminal,
                width,
            )
            tracemalloc.stop()

        if best_terminal is None:
            return self._best_partial_plan(
                ordered,
                costs,
                active,
                parent_layers,
                width,
                target_ticks,
            )

        return self._build_terminal_plan(
            ordered,
            parent_layers,
            best_terminal,
            width,
        )

    def _build_slot_actions(self, slots) -> list[list[_Action]]:
        result = []
        max_current_1 = self._max_current(1)
        max_current_3 = self._max_current(3)

        for slot in slots:
            duration = self._duration_hours(slot)
            if duration <= 0:
                result.append([])
                continue

            price = float(slot.price)
            pv_rate = self._pv_rate(slot)
            actions = []

            for phase, max_current in (
                (1, max_current_1),
                (3, max_current_3),
            ):
                for current in range(MIN_CURRENT, max_current + 1):
                    power = self._power_kw(phase, current)
                    amount = power * duration
                    amount_ticks = self._to_ticks(amount)
                    if amount_ticks <= 0:
                        continue

                    free = min(pv_rate, power) * duration
                    paid = max(0.0, amount - free)
                    if price > self.max_price and paid > 0.000001:
                        continue

                    actions.append(
                        _Action(
                            phases=phase,
                            current_a=current,
                            energy_kwh=amount,
                            energy_ticks=amount_ticks,
                            free_energy_kwh=free,
                            paid_energy_kwh=paid,
                            cost=paid * price,
                            duration_hours=duration,
                        )
                    )

            result.append(actions)

        return result

    def _relax(
        self,
        next_costs,
        next_active,
        parent,
        state,
        energy_ticks,
        candidate_cost,
        parent_energy,
        action_code,
        width,
    ) -> bool:
        index = state * width + energy_ticks
        old = next_costs[index]
        if old != _UNREACHABLE and candidate_cost >= old - _EPSILON:
            return False

        if old == _UNREACHABLE:
            next_active[state].append(energy_ticks)

        next_costs[index] = candidate_cost
        parent[index] = action_code
        return True

    def _best_partial_plan(
        self,
        ordered,
        costs,
        active,
        parent_layers,
        width,
        target_ticks,
    ) -> QuarterHourPlan:
        best_state = -1
        best_energy = -1
        best_cost = _UNREACHABLE

        for state in range(_STATE_COUNT):
            for energy_ticks in active[state]:
                cost = costs[state * width + energy_ticks]
                if (
                    energy_ticks > best_energy
                    or (
                        energy_ticks == best_energy
                        and cost < best_cost
                    )
                ):
                    best_state = state
                    best_energy = energy_ticks
                    best_cost = cost

        if best_state < 0:
            return self._empty_plan()

        actions = self._reconstruct_prefix(
            ordered,
            parent_layers,
            len(parent_layers) - 1,
            best_state,
            best_energy,
            width,
        )
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
            phase_switches=self._decode_state(best_state)[1],
        )

    def _build_terminal_plan(
        self,
        ordered,
        parent_layers,
        terminal,
        width,
    ) -> QuarterHourPlan:
        prefix = self._reconstruct_prefix(
            ordered,
            parent_layers,
            terminal.slot_index - 1,
            terminal.state,
            terminal.energy_ticks,
            width,
        )

        slot = ordered[terminal.slot_index]
        prefix.append(
            QuarterHourAction(
                index=terminal.slot_index,
                start=slot.start,
                end=slot.start
                + self._seconds_to_timedelta(
                    terminal.finish_duration_hours * 3600.0
                ),
                phases=terminal.phases,
                current_a=terminal.current_a,
                energy_kwh=terminal.energy_kwh,
                free_energy_kwh=terminal.free_energy_kwh,
                paid_energy_kwh=terminal.paid_energy_kwh,
                cost=terminal.paid_energy_kwh * float(slot.price),
            )
        )

        return QuarterHourPlan(
            actions=prefix,
            energy_kwh=sum(action.energy_kwh for action in prefix),
            free_energy_kwh=sum(
                action.free_energy_kwh for action in prefix
            ),
            paid_energy_kwh=sum(
                action.paid_energy_kwh for action in prefix
            ),
            cost=sum(action.cost for action in prefix),
            complete=True,
            missing_energy_kwh=0.0,
            phase_switches=terminal.switches,
        )

    def _reconstruct_prefix(
        self,
        ordered,
        parent_layers,
        last_slot,
        state,
        energy_ticks,
        width,
    ) -> list[QuarterHourAction]:
        actions = []

        for index in range(last_slot, -1, -1):
            code = parent_layers[index][state * width + energy_ticks]
            if code == 0:
                break
            if code == _SKIP_ACTION:
                continue

            phase, current, switched = self._decode_action(code)
            slot = ordered[index]
            duration = self._duration_hours(slot)
            power = self._power_kw(phase, current)
            amount = power * duration
            pv_rate = self._pv_rate(slot)
            free = min(pv_rate, power) * duration
            paid = max(0.0, amount - free)

            actions.append(
                QuarterHourAction(
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
            )

            amount_ticks = self._to_ticks(amount)
            energy_ticks -= amount_ticks

            switches = state // 3
            phase_index = _PHASE_INDEX[phase]
            if switched:
                switches -= 1
                previous_phase_index = 1 if phase_index == 2 else 2
            else:
                previous_phase_index = phase_index

            if previous_phase_index == 0:
                state = 0
            else:
                state = switches * 3 + previous_phase_index

        actions.reverse()
        return actions

    def _record_array_profile(
        self,
        index,
        active,
        best_terminal,
        width,
    ) -> None:
        if not self._memory_profile_enabled:
            return

        frontier_entries = sum(len(items) for items in active)
        current, peak = tracemalloc.get_traced_memory()
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        self.memory_profile.append(
            {
                "slot": index,
                "final": 0,
                "frontier_states": sum(bool(items) for items in active),
                "frontier_entries": frontier_entries,
                "nodes_created": self._profile_nodes_created,
                "best_terminal": int(best_terminal is not None),
                "rss_mb": rss_kb / 1024.0,
                "current_rss_mb": self._current_rss_mb(),
                "tracemalloc_current_mb": current / (1024.0 * 1024.0),
                "tracemalloc_peak_mb": peak / (1024.0 * 1024.0),
                "live_nodes": 0,
                "live_actions": 0,
                "array_width": width,
            }
        )

    def _record_array_final_profile(
        self,
        index,
        active,
        best_terminal,
        width,
    ) -> None:
        if not self._memory_profile_enabled:
            return

        current, peak = tracemalloc.get_traced_memory()
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        self.memory_profile.append(
            {
                "slot": index,
                "final": 1,
                "frontier_states": sum(bool(items) for items in active),
                "frontier_entries": sum(len(items) for items in active),
                "nodes_created": self._profile_nodes_created,
                "best_terminal": int(best_terminal is not None),
                "rss_mb": rss_kb / 1024.0,
                "current_rss_mb": self._current_rss_mb(),
                "tracemalloc_current_mb": current / (1024.0 * 1024.0),
                "tracemalloc_peak_mb": peak / (1024.0 * 1024.0),
                "live_nodes": 0,
                "live_actions": 0,
                "array_width": width,
            }
        )

    @staticmethod
    def _state(phase: int, switches: int) -> int:
        return switches * 3 + _PHASE_INDEX[phase]

    @staticmethod
    def _decode_state(state: int) -> tuple[int, int]:
        switches, phase_index = divmod(state, 3)
        return _PHASES[phase_index], switches

    @staticmethod
    def _action_code(
        phases: int,
        current: int,
        switched: bool,
    ) -> int:
        phase_index = 0 if phases == 1 else 1
        value = phase_index * 11 + (current - MIN_CURRENT)
        if switched:
            value += 22
        return _ACTION_BASE + value

    @staticmethod
    def _decode_action(code: int) -> tuple[int, int, bool]:
        value = code - _ACTION_BASE
        switched = value >= 22
        if switched:
            value -= 22
        phase_index, current_offset = divmod(value, 11)
        return (
            _PHASES[phase_index + 1],
            MIN_CURRENT + current_offset,
            switched,
        )

    def _better_terminal_data(
        self,
        candidate: _Terminal,
        current: _Terminal | None,
    ) -> bool:
        if current is None:
            return True
        if candidate.cost < current.cost - _EPSILON:
            return True
        if abs(candidate.cost - current.cost) <= _EPSILON:
            return candidate.switches < current.switches
        return False

    def _build_suffix_capacity(self, slots) -> list[int]:
        suffix = [0] * (len(slots) + 1)
        max_power = max(
            self._power_kw(1, self._max_current(1)),
            self._power_kw(3, self._max_current(3)),
        )

        for index in range(len(slots) - 1, -1, -1):
            slot = slots[index]
            duration = self._duration_hours(slot)
            raw = max_power * duration

            if float(slot.price) > self.max_price:
                raw = min(raw, self._pv_rate(slot) * duration)

            suffix[index] = suffix[index + 1] + self._to_ticks(raw)
        return suffix

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
        return max(
            0.0,
            float(getattr(slot, "usable_pv", 0.0)) / duration,
        )

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
        if current < MIN_CURRENT:
            return 0
        return min(MAX_CURRENT_1PH, current)

    @staticmethod
    def _current_for_power(phases: int, required_power: float) -> int:
        current = int(
            (required_power * 1000.0) / (VOLTAGE * phases)
        )
        if (
            QuarterHourOptimizer._power_kw(phases, current)
            + 0.000001
            < required_power
        ):
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
        return timedelta(seconds=seconds)
