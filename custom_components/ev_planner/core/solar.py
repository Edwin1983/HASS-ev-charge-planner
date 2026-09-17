"""Solar-only planning logic for EV Charge Planner."""

from __future__ import annotations

from ..const import PV_ROUNDING_DOWN, PV_ROUNDING_UP

EPS = 0.000001
HARD_MAX_SWITCHES = 8


def _option(planner, hour, phases):
    duration = planner._hour_duration(hour)
    if duration <= 0:
        return None

    pv = max(0.0, float(hour.pv_estimate))
    if pv <= EPS:
        return None

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
        # Naar boven afronden mag alleen een kleine netaanvulling geven.
        # Als de PV zelfs de minimale laadstroom niet kan dragen, is er
        # geen geldige solar-only laadoptie. Anders zou bijvoorbeeld
        # 20 W PV leiden tot 6 A laden (1F = 1.38 kW, 3F = 4.14 kW).
        min_power = planner._actual_power_for_current(valid[0], phases)
        if pv_rate + EPS < min_power:
            return None

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
    """
    Plan uitsluitend op basis van beschikbare PV.

    Belangrijk: solar-only heeft geen energiedoel. Het laadplan probeert
    dus niet input_number.ev_kwh_nodig te vullen. Ieder bruikbaar PV-uur
    wordt meegenomen, tenzij het fasewisselbudget een keuze onmogelijk
    maakt. De optimalisatie maximaliseert eerst gratis PV, daarna worden
    betaalde kWh geminimaliseerd en daarna fasewisselingen.
    """

    if not hours:
        return

    max_switches = min(
        HARD_MAX_SWITCHES,
        max(0, int(planner.settings.max_phase_switches)),
    )

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

        if pv <= EPS or pv < float(planner.settings.min_pv_kwh):
            options_by_index.append([])
            continue

        options = []

        for phases in (1, 3):
            option = _option(planner, hour, phases)
            if option is not None:
                options.append(option)

        options_by_index.append(options)

    # State: (laatste fase, fasewisselingen).
    # De recordwaarde is: (gratis energie, betaalde energie, parent, actie).
    states = {(0, 0): (0.0, 0.0, None, None)}
    layers = []

    for index in range(len(ordered)):
        next_states = {}

        for state, record in states.items():
            last_phase, switches = state
            free_so_far, paid_so_far, _parent, _action = record

            def put(key, candidate):
                current = next_states.get(key)
                if current is None:
                    next_states[key] = candidate
                    return

                # Eerst zoveel mogelijk gratis PV, daarna zo min mogelijk
                # netenergie, daarna blijft de eerste kandidaat staan.
                if candidate[0] > current[0] + EPS:
                    next_states[key] = candidate
                elif (
                    abs(candidate[0] - current[0]) <= EPS
                    and candidate[1] < current[1] - EPS
                ):
                    next_states[key] = candidate

            # Niet laden: fase wordt gereset, zodat een latere laadperiode
            # na een pauze geen extra fasewissel telt.
            put(
                (0, switches),
                (free_so_far, paid_so_far, state, ("skip",)),
            )

            for current_a, phases, amount, free, paid, power in options_by_index[index]:
                new_switches = switches

                if last_phase != 0 and last_phase != phases:
                    new_switches += 1

                if new_switches > max_switches:
                    continue

                put(
                    (phases, new_switches),
                    (
                        free_so_far + free,
                        paid_so_far + paid,
                        state,
                        ("full", phases, current_a, amount, free, paid),
                    ),
                )

        layers.append(next_states)
        states = next_states

    best = None
    best_score = None

    for state, record in states.items():
        _phase, switches = state
        free, paid, _parent, _action = record
        score = (free, -paid, -switches)

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
        _tag, phases, current_a, amount, free, paid = action
        power = planner._actual_power_for_current(current_a, phases)

        hour.selected = True
        hour.phases = int(phases)
        hour.charge_current_a = float(current_a)
        hour.charge_power_w = float(power * 1000.0)
        hour.charge_energy = float(amount)
        hour.free_energy = float(free)
        hour.paid_energy = float(paid)
        hour.reason = "Alleen zonneladen"
