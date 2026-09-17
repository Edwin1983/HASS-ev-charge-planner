from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

solar = '''"""Solar-only planning logic for EV Charge Planner."""

from __future__ import annotations

from ..const import PV_ROUNDING_DOWN, PV_ROUNDING_UP

EPS = 0.000001
HARD_MAX_SWITCHES = 8


def _option(planner, hour, phases):
    duration = planner._hour_duration(hour)
    if duration <= 0:
        return None
    pv = max(0.0, float(hour.pv_estimate))
    pv_rate = pv / duration
    valid = planner._valid_currents(phases)
    if not valid:
        return None
    chosen = None
    if planner.settings.pv_rounding == PV_ROUNDING_DOWN:
        for current_a in valid:
            power = planner._actual_power_for_current(current_a, phases)
            if power <= pv_rate + EPS:
                chosen = (current_a, power)
    elif planner.settings.pv_rounding == PV_ROUNDING_UP:
        for current_a in valid:
            power = planner._actual_power_for_current(current_a, phases)
            if power + EPS >= pv_rate:
                chosen = (current_a, power)
                break
        if chosen is None:
            current_a = valid[-1]
            chosen = (current_a, planner._actual_power_for_current(current_a, phases))
    else:
        raise ValueError("Ongeldige pv_rounding.")
    if chosen is None:
        return None
    current_a, power = chosen
    amount = power * duration
    free = min(pv_rate, power) * duration
    paid = max(0.0, amount - free)
    return current_a, phases, amount, free, paid, power


def apply_solar_only(planner, hours):
    if not hours:
        return
    target = float(planner.settings.energy_needed_kwh)
    max_switches = min(HARD_MAX_SWITCHES, max(0, int(planner.settings.max_phase_switches)))
    ordered = list(hours)
    ordered.sort(key=lambda hour: hour.start)
    for hour in ordered:
        hour.selected = False
        hour.charge_energy = 0.0
        hour.free_energy = 0.0
        hour.paid_energy = 0.0
        hour.charge_power_w = 0.0
        hour.charge_current_a = 0.0
        hour.reason = ""
    options_by_index = []
    for hour in ordered:
        pv = max(0.0, float(hour.pv_estimate))
        if pv < float(planner.settings.min_pv_kwh):
            options_by_index.append([])
            continue
        options = []
        for phases in (1, 3):
            option = _option(planner, hour, phases)
            if option is not None:
                options.append(option)
        options_by_index.append(options)
    states = {(0, 0, 0.0): (0.0, 0.0, None, None)}
    layers = []
    for index, hour in enumerate(ordered):
        next_states = {}
        for state, record in states.items():
            last_phase, switches, energy = state
            free_so_far, paid_so_far, _parent, _action = record
            def put(key, candidate):
                current = next_states.get(key)
                if current is None or candidate[0] > current[0] + EPS or (abs(candidate[0] - current[0]) <= EPS and candidate[1] < current[1] - EPS):
                    next_states[key] = candidate
            put((0, switches, round(energy, 9)), (free_so_far, paid_so_far, state, ("skip",)))
            remaining = target - energy
            if remaining <= EPS:
                continue
            for current_a, phases, amount, free, paid, power in options_by_index[index]:
                new_switches = switches
                if last_phase != 0 and last_phase != phases:
                    new_switches += 1
                if new_switches > max_switches:
                    continue
                if amount <= remaining + EPS:
                    new_energy = round(energy + amount, 9)
                    put((phases, new_switches, new_energy), (free_so_far + free, paid_so_far + paid, state, ("full", phases, current_a, amount, free, paid)))
                duration = planner._hour_duration(hour)
                finish_duration = remaining / power
                if finish_duration <= duration + EPS:
                    finish_free = min(free / duration, power) * finish_duration
                    finish_paid = max(0.0, remaining - finish_free)
                    new_energy = round(energy + remaining, 9)
                    put((phases, new_switches, new_energy), (free_so_far + finish_free, paid_so_far + finish_paid, state, ("finish", phases, current_a, remaining, finish_free, finish_paid)))
        layers.append(next_states)
        states = next_states
    best = None
    best_score = None
    for state, record in states.items():
        _phase, switches, energy = state
        free, paid, _parent, _action = record
        score = (min(energy, target), free, -paid, -switches)
        if best_score is None or score > best_score:
            best_score = score
            best = state
    if best is None:
        return
    state = best
    actions = [None] * len(ordered)
    for index in range(len(ordered) - 1, -1, -1):
        record = layers[index][state]
        actions[index] = record[3]
        state = record[2]
        if state is None:
            break
    for index, action in enumerate(actions):
        if action is None or action[0] == "skip":
            continue
        hour = ordered[index]
        tag, phases, current_a, amount, free, paid = action
        power = planner._actual_power_for_current(current_a, phases)
        original_start = hour.start
        original_end = hour.end
        if tag == "finish":
            hour.end = original_start + planner._duration_to_timedelta(amount / power)
            if hour.end > original_end:
                hour.end = original_end
        hour.selected = True
        hour.phases = int(phases)
        hour.charge_current_a = float(current_a)
        hour.charge_power_w = float(power * 1000.0)
        hour.charge_energy = float(amount)
        hour.free_energy = float(free)
        hour.paid_energy = float(paid)
        hour.reason = "Alleen zonneladen"
'''
(ROOT / 'custom_components/ev_planner/core/solar.py').write_text(solar, encoding='utf-8')

planner_path = ROOT / 'custom_components/ev_planner/core/planner.py'
text = planner_path.read_text(encoding='utf-8')
text = text.replace('from .logger import Logger\n\nfrom .models import Hour', 'from .logger import Logger\nfrom .solar import apply_solar_only\n\nfrom .models import Hour', 1)
text = text.replace('    PHASE_CHANGE_POWER,\n)', '    PHASE_CHANGE_POWER,\n)\n\nfrom ..const import (\n    PLANNER_MODE_NORMAL,\n    PLANNER_MODE_SOLAR_ONLY,\n    PV_ROUNDING_DOWN,\n    PV_ROUNDING_UP,\n)', 1)
text = text.replace('        max_phase_switches: int = 2,\n    ) -> None:', '        max_phase_switches: int = 2,\n        planner_mode: str = PLANNER_MODE_NORMAL,\n        pv_rounding: str = PV_ROUNDING_DOWN,\n    ) -> None:', 1)
text = text.replace('        self.max_phase_switches = int(\n            max_phase_switches\n        )\n\n        self._validate()', '        self.max_phase_switches = int(\n            max_phase_switches\n        )\n\n        self.planner_mode = str(planner_mode)\n        self.pv_rounding = str(pv_rounding)\n\n        self._validate()', 1)
text = text.replace('        if self.max_phase_switches < 0:\n\n            raise ValueError(\n                "max_phase_switches mag niet negatief zijn."\n            )', '        if self.max_phase_switches < 0:\n\n            raise ValueError(\n                "max_phase_switches mag niet negatief zijn."\n            )\n\n        if self.planner_mode not in (PLANNER_MODE_NORMAL, PLANNER_MODE_SOLAR_ONLY):\n            raise ValueError("Ongeldige planner_mode.")\n\n        if self.pv_rounding not in (PV_ROUNDING_DOWN, PV_ROUNDING_UP):\n            raise ValueError("Ongeldige pv_rounding.")', 1)
text = text.replace('        self._optimize_hours(\n            hours\n        )', '        if self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY:\n            apply_solar_only(self, hours)\n        else:\n            self._optimize_hours(\n                hours\n            )', 1)
text = text.replace('            if pv < float(\n                self.settings.min_pv_kwh\n            ):\n\n                pv = 0.0', '            if (self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY and pv < float(self.settings.min_pv_kwh)):\n                pv = 0.0', 1)
text = text.replace('        if pv < float(\n            self.settings.min_pv_kwh\n        ):\n\n            pv = 0.0', '        if (self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY and pv < float(self.settings.min_pv_kwh)):\n            pv = 0.0', 1)
old = '''            if (\n                float(hour.price) > max_price + TOLERANCE\n                and float(decision.paid_energy_kwh) > TOLERANCE\n            ):'''
new = '''            if (\n                float(hour.price) > max_price + TOLERANCE\n                and float(decision.paid_energy_kwh) > TOLERANCE\n                and not (\n                    self.settings.planner_mode == PLANNER_MODE_SOLAR_ONLY\n                    and self.settings.pv_rounding == PV_ROUNDING_UP\n                )\n            ):'''
text = text.replace(old, new, 1)
planner_path.write_text(text, encoding='utf-8')

controller_path = ROOT / 'custom_components/ev_planner/core/ev_planner.py'
text = controller_path.read_text(encoding='utf-8')
text = text.replace('    CONF_ENTITY_PLANNER_MODE,\n', '    CONF_ENTITY_PLANNER_MODE,\n    CONF_ENTITY_PV_ROUNDING,\n', 1)
text = text.replace('    DEFAULT_ENTITY_PLANNER_MODE,\n', '    DEFAULT_ENTITY_PLANNER_MODE,\n    DEFAULT_ENTITY_PV_ROUNDING,\n', 1)
text = text.replace('            "planner_mode": self.config.get(\n                CONF_ENTITY_PLANNER_MODE,\n                DEFAULT_ENTITY_PLANNER_MODE,\n            ),\n', '            "planner_mode": self.config.get(\n                CONF_ENTITY_PLANNER_MODE,\n                DEFAULT_ENTITY_PLANNER_MODE,\n            ),\n            "pv_rounding": self.config.get(\n                CONF_ENTITY_PV_ROUNDING,\n                DEFAULT_ENTITY_PV_ROUNDING,\n            ),\n', 1)
text = text.replace('        # ------------------------------------------------------------------\n        # Fasewisselingen\n', '        # ------------------------------------------------------------------\n        # Planner mode\n        # ------------------------------------------------------------------\n\n        planner_mode = self.hass.get_state(self.entities["planner_mode"])\n\n        # ------------------------------------------------------------------\n        # PV afronding\n        # ------------------------------------------------------------------\n\n        pv_rounding = self.hass.get_state(self.entities["pv_rounding"])\n\n        # ------------------------------------------------------------------\n        # Fasewisselingen\n', 1)
text = text.replace('                max_phase_switches=max_phase_switches,\n            )', '                max_phase_switches=max_phase_switches,\n                planner_mode=planner_mode,\n                pv_rounding=pv_rounding,\n            )', 1)
controller_path.write_text(text, encoding='utf-8')

print('Solar implementation applied')
