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

    kosten[bron, fase, wisselingen, pauzetoestand, k]
                                           (k = energie in roosterstappen)

De pauzetoestand z = 2 * (aantal pauzes) + (vorige slot geladen) wordt alleen
bijgehouden als er een pauzelimiet is (anders is er maar een toestand).

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
# Eindenergie mag maximaal HIT_TOL afwijken van het doel. Strenger dan de
# eindcontrole van de planner (1e-6), zodat een plan er nooit tegenaan zit.
HIT_TOL = 0.0000005
INF = float("inf")
MIN_GRID_SECONDS = 60
MAX_LATTICE_POINTS = 40000
# Bovengrens voor de tabel (aantal float64-waarden, ~8 byte elk). Daarboven
# geeft de snelle route het op en rekent de exacte DP.
MAX_TABLE_VALUES = 30_000_000


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
    max_pauses=None,
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
    # Pauzetoestand z. Zonder limiet: een enkele toestand (z = 0).
    # Met limiet P: z = 2 * pauzes + geladen_vorige_slot, Z = 2 (P + 1).
    # Pauzes tellen bij hervatten na een eerder laadmoment, dus een
    # overgeslagen slot aan het eind kost niets.
    # ------------------------------------------------------------------
    P = None if max_pauses is None else int(max_pauses)
    tracked = P is not None
    Z = 2 * (P + 1) if tracked else 1
    Z_START = 1 if tracked else 0  # net begonnen met laden, nog geen pauze

    def z_skip(z):
        return 2 * (z // 2) if tracked else 0

    def z_charge(z):
        """Toestand na laden vanuit een reeds begonnen toestand, of None."""
        if not tracked:
            return 0
        if z % 2 == 1:
            return z
        if z // 2 + 1 > P:
            return None
        return z + 3

    CH_FROM = [z for z in range(Z) if z_charge(z) is not None]
    if not CH_FROM:
        return None

    if (m + 1) * 3 * (S + 1) * Z * L > MAX_TABLE_VALUES:
        return None

    # ------------------------------------------------------------------
    # Voorwaartse tabel over de tussenliggende slots.
    # LAY[t] = toestand nadat tussenliggende slots 1..t zijn verwerkt.
    # as 1 (bron): 0 = slot 0 inactief, 1 = slot 0 1-fase, 2 = 3-fase
    # as 2 (fase): 0 = nog geen actief slot, 1 = 1-fase, 2 = 3-fase
    # ------------------------------------------------------------------
    def build_table(src_v):
        """Tabel voor een bron; eerste as heeft lengte 1 (bron-as)."""
        LAY = np.full((m + 1, 1, 3, S + 1, Z, L), INF)
        if src_v == 0:
            LAY[0, 0, 0, 0, 0, 0] = 0.0
        else:
            LAY[0, 0, src_v, 0, Z_START, 0] = 0.0

        for t in range(1, m + 1):
            i = t  # slotindex
            prev = LAY[t - 1]
            new = LAY[t]
            if tracked:
                new[...] = INF
                for z in range(Z):
                    zs = z_skip(z)
                    np.minimum(new[:, :, :, zs, :], prev[:, :, :, z, :], out=new[:, :, :, zs, :])
            else:
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

                    # starten vanuit "nog niet begonnen"
                    dst = new[:, pi, 0, Z_START, amt:]
                    cand = prev[:, 0, 0, 0, :width] + c1
                    cand -= e_a
                    np.minimum(dst, cand, out=dst)

                    for z in CH_FROM:
                        zt = z_charge(z)
                        dst = new[:, pi, :, zt, amt:]

                        cand = prev[:, pi, :, z, :width] + c1
                        cand -= e_a
                        np.minimum(dst, cand, out=dst)

                        if S >= 1:
                            cand = prev[:, other, :S, z, :width] + c1
                            cand -= e_a
                            np.minimum(dst[:, 1:, :], cand, out=dst[:, 1:, :])
        return LAY

    # Eén tabel tegelijk in het geheugen (de bronnen 0, 1 en 2 zijn
    # onafhankelijk); de tabel van de winnende bron wordt zo nodig
    # opnieuw opgebouwd voor de reconstructie.
    table_state = {"src": None, "lay": None}

    def use_table(src_v):
        if table_state["src"] != src_v:
            table_state["lay"] = None  # geheugen vrijgeven vóór de nieuwe
            table_state["lay"] = build_table(src_v)
            table_state["src"] = src_v
        return table_state["lay"]

    LAY = None

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
            if amount > target + HIT_TOL:
                break
            cost0 = 0.0 + paid * prices[0] - tiebreak * current_a
            roots.append((("full", ph, current_a), PH_IDX[ph], current_a, amount, cost0))

    cands = []  # (kosten, wissel, soort, wortel, ph, j, k)

    # finish in slot 0
    for ph in (1, 3):
        res = finish_scalar(0, ph, target)
        if res is not None:
            cands.append((res[2], 0, "finish0", None, ph, 0, 0, 0))

    # numpy-versies voor de finish-evaluatie
    dur_np = np.array(durations[1:], dtype=float)  # j = 1..n-1
    price_np = np.array(prices[1:], dtype=float)
    pv_np = np.array(pv_rates[1:], dtype=float)
    dur_ok = dur_np > 0
    dur_safe = np.where(dur_ok, dur_np, 1.0)
    expensive = price_np > max_price

    for r_index, root in enumerate(roots):
        _kind, src_v, _a, a0, cost0 = root
        LAY = use_table(src_v)
        src = 0
        t_rem = target - a0

        # --- voltooid zonder finish ("hit") ---------------------------
        k_star = int(round(t_rem / e_unit))
        if 0 <= k_star <= K and abs(t_rem - k_star * e_unit) <= HIT_TOL:
            final = LAY[m, src, :, :, :, k_star]
            for pi in range(3):
                for sw in range(S + 1):
                    for z in range(Z):
                        val = final[pi, sw, z]
                        if val < INF:
                            cands.append((cost0 + float(val), sw, "hit", r_index, pi, sw, k_star, z))

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
            lay = LAY[:m + 1, src]  # (nj, 3, S+1, Z, L)
            lay_ok = lay[:, :, :, CH_FROM, win].min(axis=3)  # (nj, 3, S+1, W)
            incoming = lay_ok[:, pi, :, :].copy()  # (nj, S+1, W)
            np.minimum(incoming[:, 0, :], lay[:, 0, 0, 0, win], out=incoming[:, 0, :])
            if S >= 1:
                np.minimum(incoming[:, 1:, :], lay_ok[:, other, :S, :], out=incoming[:, 1:, :])

            total = incoming + add[:, None, :]
            total = np.where(feas[:, None, :], total, INF)

            for sw in range(S + 1):
                sub = total[:, sw, :]
                flat = int(np.argmin(sub))
                val = float(sub.flat[flat])
                if val < INF:
                    jj, kk_i = divmod(flat, sub.shape[1])
                    cands.append((cost0 + val, sw, "finish", r_index, ph, jj + 1, int(kk[kk_i]), 0))

    # ------------------------------------------------------------------
    # Hulpfuncties voor selectie en reconstructie
    # ------------------------------------------------------------------
    def out_switches(pi_in, sw_in, ph):
        pi_out = PH_IDX[ph]
        if pi_in == 0:
            return 0
        if pi_in == pi_out:
            return sw_in
        return sw_in + 1

    def charge_sources(z_cur):
        """Toestanden z waaruit laden z_cur oplevert."""
        if not tracked:
            return [0]
        if z_cur % 2 == 0:
            return []
        out = [z_cur]
        if z_cur - 3 >= 0 and (z_cur - 3) % 2 == 0:
            out.append(z_cur - 3)
        return out

    def backtrack(src, t_layer, pi_cur, sw_cur, z_cur, k_cur, c_cur, actions):
        """Loop door de tussenliggende lagen terug naar de startstand."""
        for t in range(t_layer, 0, -1):
            i = t
            w = inner_seconds[t - 1] // g

            # overgeslagen slot
            skipped = False
            z_prevs = [0] if not tracked else ([z_cur, z_cur + 1] if z_cur % 2 == 0 else [])
            for zp in z_prevs:
                if abs(float(LAY[t - 1, src, pi_cur, sw_cur, zp, k_cur]) - c_cur) <= 1e-12:
                    actions[i] = ("skip",)
                    z_cur = zp
                    skipped = True
                    break
            if skipped:
                continue

            if pi_cur == 0:
                return False
            ph = PH_OF[pi_cur]
            other_pi = 2 if pi_cur == 1 else 1
            prevs = []
            for zp in charge_sources(z_cur):
                prevs.append((pi_cur, sw_cur, zp))
                if sw_cur >= 1:
                    prevs.append((other_pi, sw_cur - 1, zp))
            if sw_cur == 0 and z_cur == Z_START:
                prevs.append((0, 0, 0))
            done = False
            for current_a, _amount, _key, _free, paid in full_options[(i, ph)]:
                amt = ph * current_a * w
                if amt > k_cur:
                    break
                c1 = paid * prices[i]
                e_a = tiebreak * current_a
                for pp, ps, pz in prevs:
                    v = float(LAY[t - 1, src, pp, ps, pz, k_cur - amt])
                    if v == INF:
                        continue
                    if abs((v + c1 - e_a) - c_cur) <= 1e-12:
                        actions[i] = ("full", ph, current_a)
                        pi_cur, sw_cur, z_cur, k_cur, c_cur = pp, ps, pz, k_cur - amt, v
                        done = True
                        break
                if done:
                    break
            if not done:
                return False
        return k_cur == 0 and abs(c_cur) <= 1e-12

    def find_in_state(src, t_layer, k, ph, sw_out, wanted):
        """Zoek de toestand vlak voor een slot die `wanted` kosten had."""
        for pi_in in range(3):
            for sw_in in range(S + 1):
                if out_switches(pi_in, sw_in, ph) != sw_out:
                    continue
                for z_in in range(Z):
                    if pi_in != 0 and z_charge(z_in) is None:
                        continue
                    v = LAY[t_layer, src, pi_in, sw_in, z_in, k]
                    if v == INF:
                        continue
                    if abs(float(v) - wanted) <= 1e-9:
                        return pi_in, sw_in, z_in, float(v)
        return None

    # ------------------------------------------------------------------
    # Geen compleet plan? Dan het best haalbare DEELPLAN:
    # maximale energie (<= doel), daarna laagste kosten.
    # ------------------------------------------------------------------
    last = n - 1

    def collect_partial():
        out = []  # (E, kosten, wissels, soort, wortel, a, b, k)
        nonlocal_lay = None
        for r_index, root in enumerate(roots):
            _kind, src_v, _a, a0, cost0 = root
            t_rem = target - a0
            if t_rem < -EPS:
                continue
            nonlocal_lay = use_table(src_v)
            src = 0
            final_table = nonlocal_lay
            k_cap = min(K, int((t_rem + HIT_TOL) / e_unit))
            final = final_table[m, src]  # (3, S+1, Z, L)
            final_ok = final[:, :, CH_FROM, :].min(axis=2)  # (3, S+1, L)

            # laatste slot overslaan
            fin = np.isfinite(final[:, :, :, : k_cap + 1]).any(axis=(0, 1, 2))
            idx = np.nonzero(fin)[0]
            if idx.size:
                k = int(idx[-1])
                best = None
                for pi in range(3):
                    for sw in range(S + 1):
                        for z in range(Z):
                            v = float(final[pi, sw, z, k])
                            if v < INF and (best is None or (v, sw) < best):
                                best = (v, sw)
                if best is not None:
                    out.append((a0 + k * e_unit, cost0 + best[0], best[1], "pskip", r_index, 0, 0, k))

            # laatste slot (afgekapt door vertrek) volledig laden
            for ph in (1, 3):
                pi = PH_IDX[ph]
                other = 2 if pi == 1 else 1
                inc = final_ok[pi].copy()
                np.minimum(inc[0], final[0, 0, 0], out=inc[0])
                if S >= 1:
                    np.minimum(inc[1:], final_ok[other, :S], out=inc[1:])
                idxs = np.nonzero(np.isfinite(inc).any(axis=0))[0]
                if not idxs.size:
                    continue
                for current_a, amount, _key, _free, paid in full_options[(last, ph)]:
                    if a0 + amount > target + HIT_TOL:
                        break
                    k_bound = int((target + HIT_TOL - a0 - amount) / e_unit)
                    pos = int(np.searchsorted(idxs, k_bound, side="right")) - 1
                    if pos < 0:
                        continue
                    k = int(idxs[pos])
                    c1 = paid * prices[last]
                    e_a = tiebreak * current_a
                    best = None
                    for sw in range(S + 1):
                        v = float(inc[sw, k])
                        if v < INF and (best is None or (v, sw) < best):
                            best = (v, sw)
                    if best is not None:
                        cost = cost0 + ((best[0] + c1) - e_a)
                        out.append((a0 + k * e_unit + amount, cost, best[1], "pfull", r_index, ph, current_a, k))
        return out

    # ------------------------------------------------------------------
    # Beste kandidaat kiezen en plan opbouwen
    # ------------------------------------------------------------------
    actions = [("skip",)] * n

    if cands:
        best_cost = min(c[0] for c in cands)
        pool = [c for c in cands if c[0] <= best_cost + EPS]
        cost, sw_final, kind, r_index, ph_f, j, k, z_f = min(pool, key=lambda c: (c[1], c[0]))
        expected = target
    else:
        z_f = 0
        pcands = collect_partial()
        if not pcands:
            return None
        max_e = max(c[0] for c in pcands)
        if max_e <= EPS:
            return actions  # niets haalbaar: leeg plan
        pool = [c for c in pcands if c[0] >= max_e - 1e-9]
        best_cost = min(c[1] for c in pool)
        pool = [c for c in pool if c[1] <= best_cost + EPS]
        expected, cost, sw_final, kind, r_index, a_p, b_p, k = min(
            pool, key=lambda c: (c[2], c[1])
        )

    if kind == "finish0":
        res = finish_scalar(0, ph_f, target)
        actions[0] = ("finish", ph_f, res[0], res[1])
        return actions if _verify(actions, n, durations, prices, pv_rates, power_table, expected, target, S, cost, tiebreak, P) else None

    r_kind, src_v, _a, a0, cost0 = roots[r_index]
    LAY = use_table(src_v)
    src = 0
    if r_kind != "skip":
        actions[0] = ("full", r_kind[1], r_kind[2])
    t_rem = target - a0

    if kind == "hit":
        t_layer, pi_cur, sw_cur, z_cur, k_cur = m, ph_f, j, z_f, k
        c_cur = cost - cost0

    elif kind == "finish":
        t_layer = j - 1
        res = finish_scalar(j, ph_f, t_rem - k * e_unit)
        if res is None:
            return None
        found = find_in_state(src, t_layer, k, ph_f, sw_final, cost - cost0 - res[2])
        if found is None:
            return None
        pi_cur, sw_cur, z_cur, c_cur = found
        k_cur = k
        actions[j] = ("finish", ph_f, res[0], res[1])

    elif kind == "pskip":
        t_layer = m
        found = None
        for pi_in in range(3):
            for sw_in in range(S + 1):
                for z_in in range(Z):
                    v = LAY[m, src, pi_in, sw_in, z_in, k]
                    if v < INF and sw_in == sw_final and abs(float(v) - (cost - cost0)) <= 1e-9:
                        found = (pi_in, sw_in, z_in, float(v))
                        break
                if found:
                    break
            if found:
                break
        if found is None:
            return None
        pi_cur, sw_cur, z_cur, c_cur = found
        k_cur = k

    else:  # "pfull": laatste slot volledig
        ph_l, a_l = a_p, b_p
        paid_l = None
        for current_a, _amount, _key, _free, paid in full_options[(last, ph_l)]:
            if current_a == a_l:
                paid_l = paid
        if paid_l is None:
            return None
        wanted = (cost - cost0) - paid_l * prices[last] + tiebreak * a_l
        found = find_in_state(src, m, k, ph_l, sw_final, wanted)
        if found is None:
            return None
        t_layer = m
        pi_cur, sw_cur, z_cur, c_cur = found
        k_cur = k
        actions[last] = ("full", ph_l, a_l)

    if not backtrack(src, t_layer, pi_cur, sw_cur, z_cur, k_cur, c_cur, actions):
        return None

    if not _verify(actions, n, durations, prices, pv_rates, power_table, expected, target, S, cost, tiebreak, P):
        return None
    return actions


def _verify(actions, n, durations, prices, pv_rates, power_table, expected, target, S, claimed_cost, tiebreak, P=None):
    """Onafhankelijke controle: energie, fasewissels, pauzes en kosten."""
    total = 0.0
    cost = 0.0
    phase_prev = 0
    switches = 0
    pauses = 0
    started = False
    paused = False
    for i in range(n):
        act = actions[i]
        if act[0] == "skip":
            if started:
                paused = True
            continue
        if started and paused:
            pauses += 1
        started = True
        paused = False
        ph = act[1]
        power = power_table[ph][act[2]]
        if act[0] == "full":
            amount = power * durations[i]
            free = min(pv_rates[i], power) * durations[i]
            cost += max(0.0, amount - free) * prices[i] - tiebreak * act[2]
        else:
            amount = act[3]
            free = min(pv_rates[i], power) * (amount / power)
            cost += max(0.0, amount - free) * prices[i]
        total += amount
        if phase_prev and phase_prev != ph:
            switches += 1
        phase_prev = ph
    return (
        abs(total - expected) <= 2 * EPS
        and total <= target + 2 * EPS
        and switches <= S
        and (P is None or pauses <= P)
        and abs(cost - claimed_cost) <= 1e-7
    )
