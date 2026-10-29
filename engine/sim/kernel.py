"""The Numba scan: signals -> fills and exits on 1m bars.

One position (or one working order) at a time. For each signal, in bar order:
  1. skip if an order/position is still live, or the daily cap for the signal's trading day is used up
  2. entry: market fills at the open of the next strategy bar (first 1m bar in it);
     a limit works from there for `expiry_bars` strategy bars and needs trade-through by one tick,
     or fills at the open when the open already gaps past it
  3. stop/target are made absolute from the fill, then scanned on 1m bars:
     a bar that opens past the stop fills at its open (gap), a touch fills at the stop;
     a target needs trade-through by one tick; the stop wins when both hit on the same 1m bar;
     on the fill bar itself only the stop can hit (worst case), never the target;
     MAE/MFE count every bar while open, and the exit bar only up to the exit price
  4. an optional time stop closes at the open after `max_bars_held` strategy bars;
     a position still open at the end of data is closed at the last close ("end")

Prices and costs: bars are bid. Market fills are logged at the bid open and their spread is a
  charge; limit, stop and target fills are logged at the order's own price.
  R = signed(exit - entry) / risk - cost_R, with risk = |entry - stop| on those prices.
  cost_R charges, in R: the spread on market buys (a long's market entry, a short's
  time/end exit), slippage on every market fill (market entries, stops, time/end exits),
  and commission per side.
  Limit orders and stops are checked on the trader's side of the book instead, so their
  cost is a fill that comes later or not at all, never a charge: a buy limit needs the ask
  (bid + spread) to trade through and then pays its limit price (or the ask open on a gap),
  a short's stop triggers on the ask, a short's target needs the ask to reach it.

Everything that isn't a plain loop over arrays lives in simulate.py; this module must stay
numba-compilable and free of Python objects.
"""

import numpy as np

try:
    from numba import njit
except ImportError:  # pragma: no cover - numba is a dependency; this keeps tests runnable without it
    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn

# signal status
OK, BUSY, DAILY_CAP, EXPIRED, BAD_GEOMETRY, NO_DATA = 0, 1, 2, 3, 4, 5
STATUS_NAMES = ("ok", "busy", "daily_cap", "expired", "bad_geometry", "no_data")
# exit reasons
STOP, TARGET, TIME, END = 0, 1, 2, 3
EXIT_NAMES = ("stop", "target", "time", "end")


@njit(cache=True)
def scan(sig_bar, sig_entry, sig_stop_px, sig_stop_dist, sig_target_px, target_r, direction, is_limit,
         expiry_bars, max_bars_held, max_trades_per_day, sig_day,
         m1_start, m1_end, n_tf,
         o, h, l, c, spread, tick, slippage, commission):
    n = len(sig_bar)
    status = np.full(n, OK, np.int64)
    entry_i = np.full(n, -1, np.int64)
    exit_i = np.full(n, -1, np.int64)
    entry_px = np.full(n, np.nan)
    exit_px = np.full(n, np.nan)
    stop_px = np.full(n, np.nan)
    target_px = np.full(n, np.nan)
    reason = np.full(n, -1, np.int64)
    r = np.full(n, np.nan)
    cost_r = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    mfe = np.full(n, np.nan)
    bars_held = np.full(n, -1, np.int64)
    entry_bar = np.full(n, -1, np.int64)
    exit_bar = np.full(n, -1, np.int64)

    d = direction  # +1 long, -1 short
    busy_until = -1  # last 1m index occupied by an order or position
    day = -1
    day_count = 0
    n_m1 = len(o)

    for s in range(n):
        b = sig_bar[s]
        if b + 1 >= n_tf:
            status[s] = NO_DATA
            continue
        first = m1_start[b + 1]
        if first >= n_m1 or m1_end[b + 1] <= first:
            status[s] = NO_DATA
            continue
        if first <= busy_until:
            status[s] = BUSY
            continue
        if sig_day[s] != day:
            day = sig_day[s]
            day_count = 0
        if day_count >= max_trades_per_day:
            status[s] = DAILY_CAP
            continue

        # --- entry ---------------------------------------------------------------
        fill = np.nan
        fi = -1
        spread_paid = 0.0
        slip_paid = 0.0
        if not is_limit:
            fi = first
            fill = o[fi]  # bid open; a buy's spread and the slippage are charged as costs
            if d > 0:
                spread_paid = spread[fi]
            slip_paid = slippage
        else:
            lim = sig_entry[s]
            last_tf = min(b + expiry_bars, n_tf - 1)
            stop_i = m1_end[last_tf]
            for i in range(first, stop_i):
                if d > 0:
                    ask_open = o[i] + spread[i]
                    if ask_open <= lim:  # already an ask price: the spread is in the fill, not a charge
                        fill = ask_open
                        fi = i
                        break
                    if l[i] + spread[i] <= lim - tick:
                        fill = lim
                        fi = i
                        break
                else:
                    if o[i] >= lim:
                        fill = o[i]
                        fi = i
                        break
                    if h[i] >= lim + tick:
                        fill = lim
                        fi = i
                        break
            if fi < 0:
                status[s] = EXPIRED
                busy_until = stop_i - 1
                continue

        # --- stop and target, absolute ------------------------------------------
        if np.isnan(sig_stop_px[s]):
            stp = fill - d * sig_stop_dist[s]
        else:
            stp = sig_stop_px[s]
        risk = (fill - stp) * d
        if not (risk > 0):
            status[s] = BAD_GEOMETRY
            busy_until = fi
            continue
        if np.isnan(sig_target_px[s]):
            tgt = fill + d * target_r * risk
        else:
            tgt = sig_target_px[s]
        if not ((tgt - fill) * d > 0):
            status[s] = BAD_GEOMETRY
            busy_until = fi
            continue

        # --- exit scan on 1m bars --------------------------------------------------
        # the strategy bar that holds 1m bar fi
        tf_i = b + 1
        while tf_i + 1 < n_tf and m1_start[tf_i + 1] <= fi:
            tf_i += 1
        fill_tf = tf_i
        time_stop_i = n_m1  # first 1m index at which the time stop fires (open of that bar)
        if max_bars_held > 0 and fill_tf + max_bars_held < n_tf:
            time_stop_i = m1_start[fill_tf + max_bars_held]
        worst = 0.0
        best = 0.0
        ex = np.nan
        ei = -1
        why = -1
        exit_spread = 0.0
        exit_slip = 0.0
        for i in range(fi, n_m1):
            if i >= time_stop_i and i > fi:
                why = TIME
                ei = i
                ex = o[i]  # bid open; a short's cover pays the spread, both pay slippage, as costs
                if d < 0:
                    exit_spread = spread[i]
                exit_slip = slippage
                break
            if d > 0:
                lo = l[i]
                hi = h[i]
                op = o[i]
            else:  # a short exits at the ask
                lo = -(h[i] + spread[i])
                hi = -(l[i] + spread[i])
                op = -(o[i] + spread[i])
            # excursions in "signed" price space: for a short, prices are negated
            sfill = fill * d
            sstp = stp * d
            stgt = tgt * d
            if i > fi and op <= sstp:  # gapped through the stop
                why = STOP
                ei = i
                ex = op * d
                break
            if lo <= sstp:  # stop touched; wins over a target on the same bar
                why = STOP
                ei = i
                ex = stp
                break
            if sfill - lo > worst:
                worst = sfill - lo
            if i > fi:
                if hi >= stgt + tick:
                    why = TARGET
                    ei = i
                    ex = tgt
                    break
                if hi - sfill > best:
                    best = hi - sfill
        if why == STOP or why == TARGET:
            # the exit bar counts only up to the exit price: once out, the rest of the bar is not the trade's
            move = (ex - fill) * d
            if -move > worst:
                worst = -move
            if move > best:
                best = move
        if why == STOP:
            exit_slip = slippage  # a triggered stop is a market order
        if why < 0:  # still open at the end of data
            why = END
            ei = n_m1 - 1
            ex = c[ei]
            if d < 0:
                exit_spread = spread[ei]
            exit_slip = slippage

        # the strategy bar that holds the exit
        tf_e = fill_tf
        while tf_e + 1 < n_tf and m1_start[tf_e + 1] <= ei:
            tf_e += 1

        status[s] = OK
        entry_i[s] = fi
        exit_i[s] = ei
        entry_px[s] = fill
        exit_px[s] = ex
        stop_px[s] = stp
        target_px[s] = tgt
        reason[s] = why
        cost_r[s] = (spread_paid + exit_spread + slip_paid + exit_slip + 2.0 * commission) / risk
        r[s] = (ex - fill) * d / risk - cost_r[s]
        mae[s] = worst / risk
        mfe[s] = best / risk
        bars_held[s] = tf_e - fill_tf
        entry_bar[s] = fill_tf
        exit_bar[s] = tf_e
        busy_until = ei
        day_count += 1

    return (status, entry_i, exit_i, entry_px, exit_px, stop_px, target_px, reason, r, cost_r, mae, mfe,
            bars_held, entry_bar, exit_bar)
