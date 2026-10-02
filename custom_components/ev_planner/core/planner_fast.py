"""
planner_fast.py

Snelle, exacte variant van de DP in EVPlanner._optimize_hours().

IDEE
----
Elk "vol" laadblok in een tussenliggend slot levert
    energie = fasen x stroom(A) x spanning x slotduur
en alle tussenliggende slots hebben (een veelvoud van) dezelfde duur.
Alle bereikbare energieniveaus liggen daardoor op een regelmatig
rooster met stapgrootte  e_unit = spanning x g / 3600 / 1000  kWh
(g = grootste gemene deler van de slotduren in seconden, bv. 900 s).
De DP kan dan als dichte numpy-arrays worden gerekend:

    kosten[bron, fase, wisselingen, k]     (k = energie in roosterstappen)

in plaats van met dicts en tuples (zeer veel sneller, ~50x minder geheugen).

WAT NIET OP HET ROOSTER LIGT, WORDT EXACT APART BEHANDELD
---------------------------------------------------------
- slot 0 (afgekapt door "nu") : alle opties worden als wortel opgesomd;
  de rest van het probleem heeft dan alleen een verschoven doel.
  De tabel hangt alleen af van de startfase (3 bronnen), niet van het doel.
- het "finish"-blok (willekeurige laatste hoeveelheid) : exact geevalueerd
  per (slot, k) vanuit de tabel.
- het laatste slot (afgekapt door vertrek) : alleen als finish-slot.

Alleen COMPLETE plannen worden teruggegeven. In alle andere gevallen,
bij elke inconsistentie of als numpy ontbreekt, geeft deze module None
terug en gebruikt de planner de oorspronkelijke (exacte) DP.
"""

from __future__ import annotations

from math import ceil, gcd

try:  # numpy is optioneel: zonder numpy valt de planner terug op de oude DP
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

EPS = 0.000001
INF = float("inf")
MIN_GRID_SECONDS = 60
MAX_LATTICE_POINTS = 40000


def available() -> bool:
    return np is not None


def fast_optimize(
    *,
    seconds,
    durations,
    prices,
    pv_rates,
    full_options,
    valid_currents,
    power_table,
    target,
    max_switches,
    max_price,
    voltage,
    tiebreak,
):
    """
    Geef een actielijst (zelfde formaat als de oude DP) of None.

    actie = ("skip",) | ("full", fase, A) | ("finish", fase, A, kWh)
    """
    if np is None:
        return None

    n = len(durations)
    if n < 3 or target <= EPS:
        return None

    S = int(max_switches)
    m = n - 2  # aantal tussenliggende slots (1..n-2)

    # ------------------------------------------------------------------
    # Rooster bepalen uit de tussenliggende slots.
    # ------------------------------------------------------------------
    inner_seconds = []
    for i in range(1, n - 1):
        s = seconds[i]
        if abs(s - round(s)) > 1e-6 or round(s) <= 0:
            return None
        inner_seconds.append(int(round(s)))

    g = 0
    for s in inner_seconds:
        g = gcd(g, s)
    if g < MIN_GRID_SECONDS:
        return None

    e_unit = voltage * g / 3600.0 / 1000.0
    K = int((target + EPS) / e_unit)
    if K + 1 > MAX_LATTICE_POINTS:
        return None
    L = K + 1

    PH_IDX = {1: 1, 3: 2}
    PH_OF = {1: 1, 2: 3}

    # ------------------------------------------------------------------
    # Voorwaartse tabel over de tussenliggende slots.
    # LAY[t] = toestand nadat tussenliggende slots 1..t zijn verwerkt.
    # as 1 (bron): 0 = slot 0 inactief, 1 = slot 0 1-fase, 2 = 3-fase
    # as 2 (fase): 0 = nog geen actief slot, 1 = 1-fase, 2 = 3-fase
    # ------------------------------------------------------------------
    LAY = np.full((m + 1, 3, 3, S + 1, L), INF)
    LAY[0, 0, 0, 0, 0] = 0.0
    LAY[0, 1, 1, 0, 0] = 0.0
    LAY[0, 2, 2, 0, 0] = 0.0

    for t in range(1, m + 1):
        i = t  # slotindex
        prev = LAY[t - 1]
        new = LAY[t]
        new[...] = prev  # skip
        w = inner_seconds[t - 1] // g

        for ph in (1, 3):
            pi = PH_IDX[ph]
            other = 2 if pi == 1 else 1
            for current_a, _amount, _key, _free, paid in full_options[(i, ph)]:
                amt = ph * current_a * w
                if amt > K:
                    break
                c1 = paid * prices[i]
                e_a = tiebreak * current_a
                width = L - amt
                dst = new[:, pi, :, amt:]

                cand = prev[:, pi, :, :width] + c1
                cand -= e_a
                np.minimum(dst, cand, out=dst)

                cand = prev[:, 0, 0, :width] + c1
                cand -= e_a
                np.minimum(dst[:, 0, :], cand, out=dst[:, 0, :])

                if S >= 1:
                    cand = prev[:, other, :S, :width] + c1
                    cand -= e_a
                    np.minimum(dst[:, 1:, :], cand, out=dst[:, 1:, :])

    # ------------------------------------------------------------------
    # Hulpfuncties
    # ------------------------------------------------------------------
    def finish_scalar(i, ph, remaining):
        if remaining <= EPS:
            return None
        dur = durations[i]
        if dur <= 0:
            return None
        vc = valid_currents[ph]
        if not vc:
            return None
        req = int(ceil(((remaining - EPS) * 1000.0) / (voltage * float(ph) * dur)))
        if req < vc[0]:
            req = vc[0]
        if req > vc[-1]:
            return None
        power = power_table[ph][req]
        if prices[i] > max_price:
            if min(pv_rates[i], power) * dur < power * dur - EPS:
                return None
        fd = remaining / power
        if fd > dur:
            fd = dur
        amount = power * fd
        free = min(pv_rates[i], power) * fd
        paid = amount - free
        if paid < 0:
            paid = 0.0
        return req, amount, paid * prices[i]

    # ------------------------------------------------------------------
    # Wortels: wat gebeurt er in slot 0?
    # ------------------------------------------------------------------
    roots = [("skip", 0, 0, 0.0, 0.0)]  # (soort, bron, A/ph, bedrag, kosten)
    for ph in (1, 3):
        for current_a, amount, _key, _free, paid in full_options[(0, ph)]:
            if amount > target + EPS:
                break
            cost0 = 0.0 + paid * prices[0] - tiebreak * current_a
            roots.append(\n                (("full", ph, current_a), PH_IDX[ph], current_a, amount, cost0)\n            )

    cands = []  # (kosten, wissel, soort, wortel, ph, j, k)

    # finish in slot 0
    for ph in (1, 3):
        res = finish_scalar(0, ph, target)
        if res is not None:
            cands.append((res[2], 0, "finish0", None, ph, 0, 0))

    # numpy-versies voor de finish-evaluatie
    dur_np = np.array(durations[1:], dtype=float)  # j = 1..n-1
    price_np = np.array(prices[1:], dtype=float)
    pv_np = np.array(pv_rates[1:], dtype=float)
    dur_ok = dur_np > 0
    dur_safe = np.where(dur_ok, dur_np, 1.0)
    expensive = price_np > max_price

    for r_index, root in enumerate(roots):
        _kind, src, _a, a0, cost0 = root
        t_rem = target - a0

        # --- voltooid zonder finish ("hit") ---------------------------
        k_star = int(round(t_rem / e_unit))
        if 0 <= k_star <= K and abs(t_rem - k_star * e_unit) <= EPS:
            final = LAY[m, src, :, :, k_star]
            for pi in range(3):
                for sw in range(S + 1):
                    val = final[pi, sw]
                    if val < INF:
                        cands.append(\n                            (cost0 + float(val), sw, "hit", r_index, pi, sw, k_star)\n                        )

        if t_rem <= EPS:
            continue

        # --- finish in slot j >= 1 ------------------------------------
        for ph in (1, 3):
            vc = valid_currents[ph]
            if not vc:
                continue
            pi = PH_IDX[ph]
            other = 2 if pi == 1 else 1
            p_max = power_table[ph][vc[-1]]
            cap = p_max * float(dur_np.max()) + 2 * EPS
            k_hi = min(K, int((t_rem - EPS) / e_unit) + 1)
            k_lo = max(0, int(ceil((t_rem - cap) / e_unit)) - 1)
            if k_lo > k_hi:
                continue
            kk = np.arange(k_lo, k_hi + 1)
            rem = t_rem - kk * e_unit  # (W,)
            rem_ok = rem > EPS

            req = np.ceil(
                ((rem[None, :] - EPS) * 1000.0)
                / (voltage * float(ph) * dur_safe[:, None])
            )
            req = np.maximum(req, vc[0])
            feas = (req <= vc[-1]) & rem_ok[None, :] & dur_ok[:, None]
            reqi = np.clip(req, vc[0], vc[-1]).astype(int)
            power = voltage * reqi.astype(float) * float(ph) / 1000.0

            free_full = np.minimum(pv_np[:, None], power) * dur_safe[:, None]
            cond = free_full >= power * dur_safe[:, None] - EPS
            feas &= (~expensive[:, None]) | cond

            fd = np.minimum(rem[None, :] / power, dur_safe[:, None])
            amount = power * fd
            free = np.minimum(pv_np[:, None], power) * fd
            paid = np.maximum(amount - free, 0.0)
            add = paid * price_np[:, None]

            win = slice(k_lo, k_hi + 1)
            lay = LAY[:m + 1, src]  # (nj, 3, S+1, L)
            incoming = lay[:, pi, :, win].copy()  # (nj, S+1, W)
            np.minimum(incoming[:, 0, :], lay[:, 0, 0, win], out=incoming[:, 0, :])
            if S >= 1:
                np.minimum(\n                    incoming[:, 1:, :],\n                    lay[:, other, :S, win],\n                    out=incoming[:, 1:, :],\n                )

            total = incoming + add[:, None, :]
            total = np.where(feas[:, None, :], total, INF)

            for sw in range(S + 1):
                sub = total[:, sw, :]
                flat = int(np.argmin(sub))
                val = float(sub.flat[flat])
                if val < INF:
                    jj, kk_i = divmod(flat, sub.shape[1])
                    cands.append(\n                        (cost0 + val, sw, "finish", r_index, ph, jj + 1, int(kk[kk_i]))\n                    )

    if not cands:
        return None

    # ------------------------------------------------------------------
    # Beste kandidaat: laagste kosten, bij (bijna) gelijke kosten
    # de minste fasewisselingen.
    # ------------------------------------------------------------------
    best_cost = min(c[0] for c in cands)
    pool = [c for c in cands if c[0] <= best_cost + EPS]
    chosen = min(pool, key=lambda c: (c[1], c[0]))
    cost, sw_final, kind, r_index, ph_f, j, k = chosen

    actions = [("skip",)] * n

    if kind == "finish0":
        res = finish_scalar(0, ph_f, target)
        actions[0] = ("finish", ph_f, res[0], res[1])
        if _verify(actions, n, durations, power_table, target, S, EPS):
            return actions
        return None

    root = roots[r_index]
    r_kind, src, _a, a0, cost0 = root
    t_rem = target - a0
    if r_kind != "skip":
        actions[0] = ("full", r_kind[1], r_kind[2])

    # ------------------------------------------------------------------
    # Toestand vlak voor het finish-slot / aan het eind terugvinden.
    # ------------------------------------------------------------------
    def out_switches(pi_in, sw_in, ph):
        pi_out = PH_IDX[ph]
        if pi_in == 0:
            return 0
        if pi_in == pi_out:
            return sw_in
        return sw_in + 1

    if kind == "hit":
        t_layer = m
        pi_cur, sw_cur = ph_f, j  # in deze kandidaat: (fase-index, wissels)
        k_cur = k
        c_cur = cost - cost0
    else:
        t_layer = j - 1
        res = finish_scalar(j, ph_f, t_rem - k * e_unit)
        if res is None:
            return None
        target_val = cost - cost0 - res[2]
        found = None
        for pi_in in range(3):
            for sw_in in range(S + 1):
                v = LAY[t_layer, src, pi_in, sw_in, k]
                if v == INF:
                    continue
                if out_switches(pi_in, sw_in, ph_f) != sw_final:
                    continue
                if abs(float(v) - target_val) <= 1e-9:
                    found = (pi_in, sw_in)
                    break
            if found:
                break
        if found is None:
            return None
        pi_cur, sw_cur = found
        k_cur = k
        c_cur = float(LAY[t_layer, src, pi_cur, sw_cur, k_cur])
        actions[j] = ("finish", ph_f, res[0], res[1])

    # ------------------------------------------------------------------
    # Terug door de tussenliggende lagen.
    # ------------------------------------------------------------------
    for t in range(t_layer, 0, -1):
        i = t
        w = inner_seconds[t - 1] // g
        if abs(float(LAY[t - 1, src, pi_cur, sw_cur, k_cur]) - c_cur) <= 1e-12:
            actions[i] = ("skip",)
            continue
        if pi_cur == 0:
            return None
        ph = PH_OF[pi_cur]
        prevs = [(pi_cur, sw_cur)]
        if sw_cur == 0:
            prevs.append((0, 0))
        if sw_cur >= 1:
            prevs.append((2 if pi_cur == 1 else 1, sw_cur - 1))
        done = False
        for current_a, _amount, _key, _free, paid in full_options[(i, ph)]:
            amt = ph * current_a * w
            if amt > k_cur:
                break
            c1 = paid * prices[i]
            e_a = tiebreak * current_a
            for pp, ps in prevs:
                v = float(LAY[t - 1, src, pp, ps, k_cur - amt])
                if v == INF:
                    continue
                if abs((v + c1 - e_a) - c_cur) <= 1e-12:
                    actions[i] = ("full", ph, current_a)
                    pi_cur, sw_cur, k_cur, c_cur = pp, ps, k_cur - amt, v
                    done = True
                    break
            if done:
                break
        if not done:
            return None

    if k_cur != 0 or abs(c_cur) > 1e-12:
        return None

    if not _verify(actions, n, durations, power_table, target, S, EPS):
        return None
    return actions


def _verify(actions, n, durations, power_table, target, S, eps):
    """Onafhankelijke controle: energie, fasewisselingen."""
    total = 0.0
    phase_prev = 0
    switches = 0
    for i in range(n):
        act = actions[i]
        if act[0] == "skip":
            continue
        ph = act[1]
        if act[0] == "full":
            total += power_table[ph][act[2]] * durations[i]
        else:
            total += act[3]
        if phase_prev and phase_prev != ph:
            switches += 1
        phase_prev = ph
    return abs(total - target) <= 2 * eps and switches <= S
