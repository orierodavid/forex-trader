"""
Timeframe Screener — S/R Rejection Strategy across intervals & pairs
------------------------------------------------------------------------
Tests the baseline support/resistance rejection strategy (no trend filter,
no touch-count filter, no session-hour filter — the clean v1 logic) across
several candle timeframes and your three best-evidenced pairs, to find out
which timeframe the strategy actually works best on.

IMPORTANT: each timeframe pulls the same NUMBER of bars (5000, one API
call), not the same calendar PERIOD. A 15-minute chart covers far fewer
real days than a 4-hour chart for the same bar count. Each result prints
its actual date range so you can see this — don't compare raw signal
counts across timeframes without checking the period too.

Uses 1 Twelve Data API credit per pair/timeframe combo — 18 total for
3 pairs x 6 timeframes.
"""

import requests
import time
from datetime import datetime

TWELVE_DATA_API_KEY = "dbe551d12fab420d9c5f54c869cab829"

PAIRS = ["EUR/USD", "GBP/USD", "USD/CHF"]
TIMEFRAMES = ["15min", "30min", "45min", "1h", "2h", "4h"]

OUTPUT_SIZE = 5000

SWING_LOOKBACK = 5
LEVEL_LOOKBACK_BARS = 300
REJECTION_BUFFER_ATR = 0.1
ATR_PERIOD = 14
SL_BUFFER_ATR = 0.3
RR_RATIO = 2.0

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"


# ── DATA FETCH (single call, no pagination) ─────────────────────────────
def fetch_candles(pair: str, interval: str):
    params = {
        "symbol": pair,
        "interval": interval,
        "outputsize": OUTPUT_SIZE,
        "apikey": TWELVE_DATA_API_KEY,
        "order": "ASC",
    }
    resp = requests.get(TWELVE_DATA_URL, params=params, timeout=30)
    data = resp.json()
    if "values" not in data or not data["values"]:
        print(f"    [no data for {pair} @ {interval} — API response: {data}]")
        return [], [], [], []
    candles = data["values"]
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    times = [c["datetime"] for c in candles]
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


# ── BACKTEST ENGINE (baseline S/R, no extra filters) ────────────────────
def backtest(times, highs, lows, closes):
    min_len = LEVEL_LOOKBACK_BARS + SWING_LOOKBACK * 2 + 10
    if len(closes) < min_len:
        return [], None, None

    atr_vals = atr_series(highs, lows, closes, ATR_PERIOD)
    swing_highs, swing_lows = find_swing_points(highs, lows, SWING_LOOKBACK)

    trades = []
    start = min_len

    for i in range(start, len(closes) - 1):
        a = atr_vals[i]
        if a is None:
            continue

        window_start = i - LEVEL_LOOKBACK_BARS
        buffer_dist = a * REJECTION_BUFFER_ATR

        resistance = None
        for idx in sorted([k for k in swing_highs if window_start <= k < i], reverse=True):
            level = swing_highs[idx]
            if level <= closes[i]:
                continue
            if not any(closes[k] > level for k in range(idx + 1, i)):
                resistance = level
                break

        support = None
        for idx in sorted([k for k in swing_lows if window_start <= k < i], reverse=True):
            level = swing_lows[idx]
            if level >= closes[i]:
                continue
            if not any(closes[k] < level for k in range(idx + 1, i)):
                support = level
                break

        direction = None
        level_used = None

        if resistance is not None:
            if highs[i] >= resistance - buffer_dist and closes[i] < resistance:
                direction = "SELL"
                level_used = resistance

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


def summarize(pair, interval, trades, start_time, end_time):
    total = len(trades)
    wins = trades.count("WIN")
    losses = total - wins
    win_rate = (wins / total * 100) if total else 0
    expectancy_r = (wins * RR_RATIO - losses * 1) / total if total else 0

    print(f"\n--- {pair} @ {interval} ---")
    if total == 0:
        print("No signals (or insufficient data).")
    else:
        print(f"Period: {start_time} to {end_time}")
        print(f"Signals: {total}  Win rate: {win_rate:.1f}%  Expectancy: {expectancy_r:.2f}R")

    return {
        "pair": pair, "interval": interval, "total": total,
        "win_rate": win_rate, "expectancy_r": expectancy_r,
        "start": start_time, "end": end_time,
    }


def main():
    print(f"Screening {len(PAIRS)} pairs x {len(TIMEFRAMES)} timeframes "
          f"({len(PAIRS) * len(TIMEFRAMES)} combos)...")

    results = []
    for pair in PAIRS:
        for interval in TIMEFRAMES:
            try:
                times, highs, lows, closes = fetch_candles(pair, interval)
                trades, start_time, end_time = backtest(times, highs, lows, closes)
                results.append(summarize(pair, interval, trades, start_time, end_time))
            except Exception as e:
                print(f"\n--- {pair} @ {interval} ---\nERROR: {e}")

            time.sleep(8)  # stay under Twelve Data's 8 requests/minute free-tier limit

    ranked = sorted(
        [r for r in results if r["total"] >= 20],
        key=lambda r: r["expectancy_r"],
        reverse=True
    )

    print("\n\n=== RANKED (min. 20 signals) ===")
    if not ranked:
        print("No combo reached 20+ signals.")
    for r in ranked:
        print(f"{r['pair']} @ {r['interval']}: {r['total']} signals, "
              f"{r['win_rate']:.1f}% win rate, {r['expectancy_r']:.2f}R  "
              f"[{r['start']} to {r['end']}]")

    print("\nNote: best-case estimates before spread/slippage/commission. "
          "Timeframes cover DIFFERENT calendar periods for the same bar "
          "count — check the printed date range, not just the ranking.")


if __name__ == "__main__":
    main()
