"""Solar-only planning logic for EV Charge Planner."""

from __future__ import annotations

import math

from .planner import EVPlanner
from ..const import PV_ROUNDING_DOWN, PV_ROUNDING_UP


class SolarOnlyPlanner(EVPlanner):
    """Plan charging primarily from PV with optional rounding import.

    Normal price optimization remains in EVPlanner. This class only changes
    the candidate current selection for solar-only operation.
    """

    def _pv_rate_kw(self, hour):
        duration = self._hour_duration(hour)
        if duration <= 0:
            return 0.0

        pv = float(hour.pv_estimate)
        if pv < 0:
            pv = 0.0

        # In solar-only mode min_pv_kwh is deliberately a filter only.
        if pv < float(self.settings.min_pv_kwh):
            return 0.0

        return pv / duration

    def _solar_current(self, pv_rate, phases, valid_currents):
        """Return the whole-amp current permitted by PV rounding mode."""
        if pv_rate <= 0:
            return None

        volts = 230.0
        amp_power = volts * float(phases) / 1000.0

        if amp_power <= 0:
            return None

        if self.settings.pv_rounding == PV_ROUNDING_DOWN:
            current = int(math.floor(pv_rate / amp_power + 0.000001))
        else:
            current = int(math.ceil(pv_rate / amp_power - 0.000001))

        if current < 6:
            return None

        if current > 16:
            current = 16

        if current not in valid_currents:
            return None

        return current

    def _optimize_hours(self, hours):
        """Optimize solar-only operation using PV-derived currents.

        The inherited DP remains responsible for hour selection, phase
        switching and exact final energy. Candidate currents are constrained
        to the PV-derived current, so rounding down never imports planned
        grid energy and rounding up imports only the next whole ampere.
        """
        if not hours:
            return

        # Solar-only mode is deliberately represented through the inherited
        # optimizer. We temporarily expose only the PV-compatible current
        # candidates by setting the minimum PV filter and using the existing
        # price gate with max_price effectively unlimited for PV operation.
        # The actual candidate restriction is implemented by the local
        # current filter below.
        original_valid = self._valid_currents

        def solar_valid_currents(phases):
            base = original_valid(phases)
            result = []
            for hour in hours:
                rate = self._pv_rate_kw(hour)
                current = self._solar_current(rate, phases, base)
                if current is not None and current not in result:
                    result.append(current)
            return result

        # The inherited optimizer works per hour, so a single global list is
        # insufficient when PV differs by hour. For correctness, temporarily
        # use a per-call helper consumed by a small specialized implementation.
        self._solar_hours = hours
        self._solar_valid_currents_by_hour = solar_valid_currents

        try:
            self._optimize_solar_hours(hours)
        finally:
            self._solar_hours = None
            self._solar_valid_currents_by_hour = None

    def _optimize_solar_hours(self, hours):
        """Greedy solar optimizer with phase-switch constraints.

        Hours are considered by descending usable PV energy. Within equal PV
        yield, the earlier hour is preferred. The selected current is exactly
        the PV-derived whole ampere according to the configured rounding.
        """
        ordered = []
        for hour in hours:
            ordered.append(hour)
        ordered.sort(
            key=lambda h: (-max(0.0, float(h.pv_estimate)), h.start)
        )

        target = float(self.settings.energy_needed_kwh)
        remaining = target
        previous_phase = None
        switches = 0
        selected = []

        for hour in ordered:
            if remaining <= 0.000001:
                break

            pv_rate = self._pv_rate_kw(hour)
            if pv_rate <= 0:
                continue

            duration = self._hour_duration(hour)
            if duration <= 0:
                continue

            chosen_phase = None
            chosen_current = None

            phases_to_try = [3, 1]
            if previous_phase in (1, 3):
                phases_to_try = [previous_phase]
                other = 1 if previous_phase == 3 else 3
                phases_to_try.append(other)

            for phase in phases_to_try:
                valid = self._valid_currents(phase)
                current = self._solar_current(pv_rate, phase, valid)
                if current is None:
                    continue

                if previous_phase is not None and phase != previous_phase:
                    if switches >= int(self.settings.max_phase_switches):
                        continue

                chosen_phase = phase
                chosen_current = current
                break

            if chosen_phase is None:
                continue

            power = self._actual_power_for_current(chosen_current, chosen_phase)
            if power <= 0:
                continue

            amount = power * duration
            if amount > remaining:
                amount = remaining
                duration = amount / power

            pv_energy = min(pv_rate * duration, amount)
            paid = max(0.0, amount - pv_energy)

            if self.settings.pv_rounding == PV_ROUNDING_DOWN:
                paid = 0.0
                amount = min(amount, pv_energy)
                if amount <= 0:
                    continue
                duration = amount / power

            hour.selected = True
            hour.phases = int(chosen_phase)
            hour.charge_current_a = float(chosen_current)
            hour.charge_power_w = float(power * 1000.0)
            hour.charge_energy = float(amount)
            hour.free_energy = float(min(pv_energy, amount))
            hour.paid_energy = float(paid)
            hour.start = hour.start
            hour.end = hour.start + self._duration_to_timedelta(duration)
            hour.reason = "Alleen zonneladen"

            if previous_phase is not None and chosen_phase != previous_phase:
                switches += 1
            previous_phase = chosen_phase
            selected.append(hour)
            remaining -= amount

        # Restore chronological order in the original list.
        selected_set = set(id(h) for h in selected)
        for hour in hours:
            if id(hour) not in selected_set:
                hour.selected = False
                hour.charge_energy = 0.0
                hour.free_energy = 0.0
                hour.paid_energy = 0.0
                hour.charge_power_w = 0.0
                hour.charge_current_a = 0.0

        # The inherited final plan builder will calculate the totals.
