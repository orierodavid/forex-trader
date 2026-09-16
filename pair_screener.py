"""
Pair Screener — Trend + RSI Pullback Strategy, session-filtered
------------------------------------------------------------------
Tests the exact same strategy (with the crossover fix and session
filter) against several candidate pairs, using ~2 years of history
per pair (not just 6 months), so we can pick a real replacement for
USD/JPY based on evidence rather than a guess.

Uses roughly 3 Twelve Data API credits per pair (paginated fetches),
so ~18 credits total for 6 pairs — trivial against an 800/day limit.
"""

import requests
from datetime import datetime, timedelta

TWELVE_DATA_API_KEY = "dbe551d12fab420d9c5f54c869cab829"

# EUR/USD included as a known baseline for comparison.
# USD/JPY included too, so you can see it re-confirmed as bad, not just
# taken on faith. The others are liquidity-comparable candidates.
CANDIDATE_PAIRS = ["EUR/USD", "GBP/USD", "USD/CHF", "AUD/USD", "USD/CAD", "USD/JPY"]

INTERVAL = "1h"
CHUNK_SIZE = 5000
TARGET_BARS = 14000   # roughly ~2 years of 1H candles

EMA_FAST = 50
EMA_SLOW = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 3.0

SESSION_START_HOUR = 12
SESSION_END_HOUR = 16

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"


# ── DATA FETCH (paginated for extended history) ─────────────────────────
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
            break  # no more data available

    # Dedupe and sort ascending by time
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
def ema_series(values, period):
    k = 2 / (period + 1)
    out = [values[0]]
    for price in values[1:]:
        out.append(price * k + out[-1] * (1 - k))
    return out


def rsi_series(values, period):
    gains, losses = [0], [0]
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    out = [None] * period

    for i in range(period, len(values)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        out.append(100 - (100 / (1 + rs)))
    return out


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


# ── BACKTEST ENGINE (crossover-only, session-filtered) ───────────────────
def backtest_pair(pair: str):
    times, highs, lows, closes = fetch_extended_candles(pair)

    if len(closes) < EMA_SLOW + 5:
        return [], None, None

    ema_fast_vals = ema_series(closes, EMA_FAST)
    ema_slow_vals = ema_series(closes, EMA_SLOW)
    rsi_vals = rsi_series(closes, RSI_PERIOD)
    atr_vals = atr_series(highs, lows, closes, ATR_PERIOD)

    trades = []
    start = EMA_SLOW + 5

    for i in range(start, len(closes) - 1):
        fast, slow = ema_fast_vals[i], ema_slow_vals[i]
        r, a = rsi_vals[i], atr_vals[i]
        if r is None or a is None:
            continue

        r_prev = rsi_vals[i - 1]
        if r_prev is None:
            continue

        candle_hour = datetime.strptime(times[i], "%Y-%m-%d %H:%M:%S").hour
        if not (SESSION_START_HOUR <= candle_hour < SESSION_END_HOUR):
            continue

        price = closes[i]
        direction = None

        if fast > slow and r < 45 and r_prev >= 45:
            direction = "BUY"
        elif fast < slow and r > 55 and r_prev <= 55:
            direction = "SELL"

        if direction is None:
            continue

        if direction == "BUY":
            sl = price - ATR_SL_MULT * a
            tp = price + ATR_TP_MULT * a
        else:
            sl = price + ATR_SL_MULT * a
            tp = price - ATR_TP_MULT * a

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
    expectancy_r = (wins * 2 - losses * 1) / total if total else 0

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
    print(f"Screening {len(CANDIDATE_PAIRS)} pairs over ~2 years of history each...\n")
    results = []
    for pair in CANDIDATE_PAIRS:
        try:
            trades, start_time, end_time = backtest_pair(pair)
            results.append(summarize(pair, trades, start_time, end_time))
        except Exception as e:
            print(f"\n=== {pair} ===\nERROR: {e}")

    # Rank by expectancy, but only among pairs with a reasonably sized sample
    ranked = sorted(
        [r for r in results if r["total"] >= 20],
        key=lambda r: r["expectancy_r"],
        reverse=True
    )

    print("\n\n=== RANKED (min. 20 signals) ===")
    if not ranked:
        print("No pair reached 20+ signals — consider a longer period or looser filter.")
    for r in ranked:
        print(f"{r['pair']}: {r['total']} signals, {r['win_rate']:.1f}% win rate, "
              f"{r['expectancy_r']:.2f}R expectancy/trade")

    print("\nNote: best-case estimates before spread/slippage/commission. "
          "Pick a positive-expectancy pair with a reasonable sample size, "
          "not just the single highest number.")


if __name__ == "__main__":
    main()
