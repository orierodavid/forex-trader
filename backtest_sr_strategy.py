"""
Support/Resistance Rejection Strategy — Screener
----------------------------------------------------------------
A different approach from trend+RSI: identifies real swing highs/lows
(structural support/resistance) and looks for price testing a level
and getting REJECTED (bouncing off support, or failing at resistance),
confirmed by the candle closing back on the right side of the level.

Tests across several candidate pairs, including XAU/USD (gold), using
~2 years of history each, session-filtered to the London/NY overlap.

RULES:
  - Swing points: a candle is a swing high/low if it's the highest/lowest
    of the 5 candles before AND after it (a standard "fractal" definition)
  - A support/resistance level stays "active" until price closes clearly
    through it
  - Entry: price wicks into an active level and the candle CLOSES back
    on the origin side (rejection), within the session window
  - Stop-loss: just beyond the level (+ a small ATR buffer)
  - Take-profit: 2x the stop distance (1:2 risk-reward, same as before)

Uses ~3 Twelve Data API credits per pair (paginated fetches).
"""

import requests
from datetime import datetime, timedelta

TWELVE_DATA_API_KEY = "dbe551d12fab420d9c5f54c869cab829"

CANDIDATE_PAIRS = ["EUR/USD", "GBP/USD", "USD/CHF", "XAU/USD", "AUD/USD", "USD/CAD", "USD/JPY"]

INTERVAL = "1h"
CHUNK_SIZE = 5000
TARGET_BARS = 14000   # roughly ~2 years of 1H candles

SWING_LOOKBACK = 5        # candles on each side to confirm a swing point
LEVEL_LOOKBACK_BARS = 300 # how far back to search for active levels
REJECTION_BUFFER_ATR = 0.1  # how close a wick must get to the level (as a fraction of ATR)
ATR_PERIOD = 14
SL_BUFFER_ATR = 0.3       # extra room beyond the level for the stop-loss
RR_RATIO = 2.0            # take-profit distance = RR_RATIO x stop distance

SESSION_START_HOUR = 12
SESSION_END_HOUR = 16

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"


# ── DATA FETCH (paginated) ───────────────────────────────────────────────
def fetch_extended_candles(pair: str):
    all_rows = []
    end_date = None

    while len(all_rows) < TARGET_BARS:
        params = {
            "symbol": pair,
            "interval": INTERVAL,
            "outputsize": CHUNK_SIZE,
            "apikey": TWELVE_DATA_API_KEY,
            "order": "DESC",
        }
        if end_date:
            params["end_date"] = end_date

        resp = requests.get(TWELVE_DATA_URL, params=params, timeout=30)
        data = resp.json()
        if "values" not in data or not data["values"]:
            break

        values = data["values"]
        all_rows.extend(values)

        earliest = datetime.strptime(values[-1]["datetime"], "%Y-%m-%d %H:%M:%S")
        end_date = (earliest - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        if len(values) < CHUNK_SIZE:
            break

    seen = {}
    for row in all_rows:
        seen[row["datetime"]] = row
    ordered = sorted(seen.values(), key=lambda r: r["datetime"])

    closes = [float(r["close"]) for r in ordered]
    highs = [float(r["high"]) for r in ordered]
    lows = [float(r["low"]) for r in ordered]
    times = [r["datetime"] for r in ordered]
    return times, highs, lows, closes


# ── INDICATORS ───────────────────────────────────────────────────────────
def atr_series(highs, lows, closes, period):
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    out = [None] * (period - 1)
    out.append(sum(trs[:period]) / period)
    for i in range(period, len(trs)):
        out.append((out[-1] * (period - 1) + trs[i]) / period)
    return out


def find_swing_points(highs, lows, lookback):
    """Fractal-based swing highs/lows: index -> price."""
    swing_highs = {}
    swing_lows = {}
    for i in range(lookback, len(highs) - lookback):
        window_hi = highs[i - lookback:i + lookback + 1]
        if highs[i] == max(window_hi):
            swing_highs[i] = highs[i]
        window_lo = lows[i - lookback:i + lookback + 1]
        if lows[i] == min(window_lo):
            swing_lows[i] = lows[i]
    return swing_highs, swing_lows


# ── BACKTEST ENGINE ──────────────────────────────────────────────────────
def backtest_pair(pair: str):
    times, highs, lows, closes = fetch_extended_candles(pair)

    if len(closes) < LEVEL_LOOKBACK_BARS + SWING_LOOKBACK * 2 + 10:
        return [], None, None

    atr_vals = atr_series(highs, lows, closes, ATR_PERIOD)
    swing_highs, swing_lows = find_swing_points(highs, lows, SWING_LOOKBACK)

    trades = []
    start = LEVEL_LOOKBACK_BARS + SWING_LOOKBACK * 2

    for i in range(start, len(closes) - 1):
        a = atr_vals[i]
        if a is None:
            continue

        candle_hour = datetime.strptime(times[i], "%Y-%m-%d %H:%M:%S").hour
        if not (SESSION_START_HOUR <= candle_hour < SESSION_END_HOUR):
            continue

        window_start = i - LEVEL_LOOKBACK_BARS
        # Nearest active resistance: most recent swing high ABOVE current close
        # that hasn't been closed through since it formed.
        resistance = None
        for idx in sorted([k for k in swing_highs if window_start <= k < i], reverse=True):
            level = swing_highs[idx]
            if level <= closes[i]:
                continue
            broken = any(closes[k] > level for k in range(idx + 1, i))
            if not broken:
                resistance = level
                break

        support = None
        for idx in sorted([k for k in swing_lows if window_start <= k < i], reverse=True):
            level = swing_lows[idx]
            if level >= closes[i]:
                continue
            broken = any(closes[k] < level for k in range(idx + 1, i))
            if not broken:
                support = level
                break

        buffer_dist = a * REJECTION_BUFFER_ATR
        direction = None
        level_used = None

        # Resistance rejection: wick reaches into resistance, close stays below it
        if resistance is not None:
            if highs[i] >= resistance - buffer_dist and closes[i] < resistance:
                direction = "SELL"
                level_used = resistance

        # Support rejection: wick reaches into support, close stays above it
        if direction is None and support is not None:
            if lows[i] <= support + buffer_dist and closes[i] > support:
                direction = "BUY"
                level_used = support

        if direction is None:
            continue

        sl_buffer = a * SL_BUFFER_ATR
        price = closes[i]

        if direction == "BUY":
            sl = level_used - sl_buffer
            risk = price - sl
            tp = price + risk * RR_RATIO
        else:
            sl = level_used + sl_buffer
            risk = sl - price
            tp = price - risk * RR_RATIO

        if risk <= 0:
            continue

        outcome = None
        for j in range(i + 1, len(closes)):
            hi, lo = highs[j], lows[j]
            if direction == "BUY":
                if lo <= sl:
                    outcome = "LOSS"
                    break
                if hi >= tp:
                    outcome = "WIN"
                    break
            else:
                if hi >= sl:
                    outcome = "LOSS"
                    break
                if lo <= tp:
                    outcome = "WIN"
                    break

        if outcome is not None:
            trades.append(outcome)

    return trades, times[start], times[-1]


def summarize(pair: str, trades: list, start_time, end_time):
    total = len(trades)
    wins = trades.count("WIN")
    losses = total - wins
    win_rate = (wins / total * 100) if total else 0
    expectancy_r = (wins * RR_RATIO - losses * 1) / total if total else 0

    print(f"\n=== {pair} ===")
    if total == 0:
        print("No signals found (or insufficient data).")
    else:
        print(f"Period: {start_time} to {end_time}")
        print(f"Total signals: {total}")
        print(f"Wins: {wins}  Losses: {losses}  Win rate: {win_rate:.1f}%")
        print(f"Expectancy: {expectancy_r:.2f}R per trade (before spread/slippage)")

    return {
        "pair": pair, "total": total, "wins": wins, "losses": losses,
        "win_rate": win_rate, "expectancy_r": expectancy_r
    }


def main():
    print(f"Screening {len(CANDIDATE_PAIRS)} pairs with a support/resistance "
          f"rejection strategy over ~2 years each...\n")
    results = []
    for pair in CANDIDATE_PAIRS:
        try:
            trades, start_time, end_time = backtest_pair(pair)
            results.append(summarize(pair, trades, start_time, end_time))
        except Exception as e:
            print(f"\n=== {pair} ===\nERROR: {e}")

    ranked = sorted(
        [r for r in results if r["total"] >= 20],
        key=lambda r: r["expectancy_r"],
        reverse=True
    )

    print("\n\n=== RANKED (min. 20 signals) ===")
    if not ranked:
        print("No pair reached 20+ signals.")
    for r in ranked:
        print(f"{r['pair']}: {r['total']} signals, {r['win_rate']:.1f}% win rate, "
              f"{r['expectancy_r']:.2f}R expectancy/trade")

    print("\nNote: best-case estimates before spread/slippage/commission.")


if __name__ == "__main__":
    main()
