"""
Breakout Strategy Backtest — XAU/USD (Gold)
----------------------------------------------------------------
Original code, inspired by the general category of technique commercial
gold EAs like "Gold Reaper" use — NOT a copy of any proprietary product.

OPPOSITE LOGIC from your current rejection strategy:
  - Rejection strategy: price wicks INTO a level and bounces AWAY (fade the level)
  - Breakout strategy:  price CLOSES THROUGH a level with momentum (follow the move)

RULES:
  - Find real structural swing highs/lows (fractal: highest/lowest of the
    5 candles on each side)
  - A level stays "active" until price closes clearly through it
  - BREAKOUT signal: the candle CLOSES beyond an active level (not just
    wicks past it) — this is genuine confirmation, not a fakeout wick
  - MOMENTUM FILTER: the breakout candle's range must be at least 1.2x
    the current ATR — filters weak, low-conviction breaks (this is the
    "fake breakout filtering" concept commercial gold EAs advertise)
  - Stop-loss: just inside the broken level (a false breakout reversing
    back through triggers an exit)
  - Take-profit: 2x the stop distance (1:2 risk-reward, same as your
    other strategies, for a fair comparison)

Tests XAU/USD at both 1H (the timeframe Gold Reaper itself actually runs
on) and 4H (to compare against your current live bot's timeframe).
"""

import requests
from datetime import datetime, timedelta

TWELVE_DATA_API_KEY = "dbe551d12fab420d9c5f54c869cab829"

PAIR = "XAU/USD"
TIMEFRAMES = ["1h", "4h"]

CHUNK_SIZE = 5000
TARGET_BARS = 8000   # as much history as available

SWING_LOOKBACK = 5
LEVEL_LOOKBACK_BARS = 300
ATR_PERIOD = 14
MOMENTUM_MULT = 1.2      # breakout candle range must be >= this x ATR
SL_BUFFER_ATR = 0.3
RR_RATIO = 2.0

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"


# ── DATA FETCH (paginated) ───────────────────────────────────────────────
def fetch_extended_candles(interval: str):
    all_rows = []
    end_date = None

    while len(all_rows) < TARGET_BARS:
        params = {
            "symbol": PAIR,
            "interval": interval,
            "outputsize": CHUNK_SIZE,
            "apikey": TWELVE_DATA_API_KEY,
            "order": "DESC",
        }
        if end_date:
            params["end_date"] = end_date

        resp = requests.get(TWELVE_DATA_URL, params=params, timeout=30)
        data = resp.json()
        if "values" not in data or not data["values"]:
            print(f"    [no more data — API response: {data}]")
            break

        values = data["values"]
        all_rows.extend(values)

        step = timedelta(hours=4) if interval == "4h" else timedelta(hours=1)
        earliest = datetime.strptime(values[-1]["datetime"], "%Y-%m-%d %H:%M:%S")
        end_date = (earliest - step).strftime("%Y-%m-%d %H:%M:%S")

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
def backtest(interval: str):
    times, highs, lows, closes = fetch_extended_candles(interval)

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

        candle_range = highs[i] - lows[i]
        if candle_range < MOMENTUM_MULT * a:
            continue  # too weak, skip — not a real momentum breakout

        window_start = i - LEVEL_LOOKBACK_BARS

        resistance = None
        for idx in sorted([k for k in swing_highs if window_start <= k < i], reverse=True):
            level = swing_highs[idx]
            if level <= closes[i - 1]:
                continue  # already broken before this candle, not a fresh breakout
            if not any(closes[k] > level for k in range(idx + 1, i)):
                resistance = level
                break

        support = None
        for idx in sorted([k for k in swing_lows if window_start <= k < i], reverse=True):
            level = swing_lows[idx]
            if level >= closes[i - 1]:
                continue
            if not any(closes[k] < level for k in range(idx + 1, i)):
                support = level
                break

        direction = None
        level_used = None

        # Bullish breakout: this candle CLOSES above active resistance
        if resistance is not None and closes[i] > resistance:
            direction = "BUY"
            level_used = resistance

        # Bearish breakdown: this candle CLOSES below active support
        if direction is None and support is not None and closes[i] < support:
            direction = "SELL"
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


def summarize(interval, trades, start_time, end_time):
    total = len(trades)
    wins = trades.count("WIN")
    losses = total - wins
    win_rate = (wins / total * 100) if total else 0
    expectancy_r = (wins * RR_RATIO - losses * 1) / total if total else 0

    print(f"\n=== XAU/USD @ {interval} (breakout strategy) ===")
    if total == 0:
        print("No signals found.")
    else:
        print(f"Period: {start_time} to {end_time}")
        print(f"Signals: {total}  Win rate: {win_rate:.1f}%  Expectancy: {expectancy_r:.2f}R")

    return {"interval": interval, "total": total, "win_rate": win_rate, "expectancy_r": expectancy_r}


def main():
    print("Testing breakout strategy on XAU/USD (original code, "
          "same general concept as commercial gold breakout EAs)...\n")
    results = []
    for interval in TIMEFRAMES:
        trades, start_time, end_time = backtest(interval)
        results.append(summarize(interval, trades, start_time, end_time))

    print("\n=== SUMMARY ===")
    for r in results:
        print(f"XAU/USD @ {r['interval']}: {r['total']} signals, "
              f"{r['win_rate']:.1f}% win rate, {r['expectancy_r']:.2f}R expectancy")

    print("\nNote: best-case estimate before spread/slippage/commission. "
          "Compare against your current USD/CHF @ 4H rejection strategy "
          "(43.5% win rate, +0.31R) to see if this is genuinely better, "
          "not just different.")


if __name__ == "__main__":
    main()
