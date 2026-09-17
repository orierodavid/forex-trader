"""
Multi-Timeframe Confirmation Test — USD/CHF 4H S/R + Daily Trend Filter
----------------------------------------------------------------------------
Tests whether requiring the DAILY trend to agree improves the already-
validated USD/CHF @ 4H support/resistance strategy (baseline: ~556 trades,
39.6% win rate, +0.19R over ~2.7 years).

Rule added: only take a support bounce (BUY) if the DAILY close is above
the daily 50 EMA (daily uptrend), and only take a resistance rejection
(SELL) if the DAILY close is below the daily 50 EMA (daily downtrend).
Uses the PREVIOUS day's closed daily candle only — never today's
still-forming one — to avoid lookahead bias.

Uses ~3-4 Twelve Data API credits total (paginated 4H fetch + one daily
fetch).
"""

import requests
from datetime import datetime, timedelta

TWELVE_DATA_API_KEY = "dbe551d12fab420d9c5f54c869cab829"
PAIR = "USD/CHF"

CHUNK_SIZE = 5000
TARGET_4H_BARS = 6000   # ~2.7+ years of 4H candles

SWING_LOOKBACK = 5
LEVEL_LOOKBACK_BARS = 300
REJECTION_BUFFER_ATR = 0.1
ATR_PERIOD = 14
SL_BUFFER_ATR = 0.3
RR_RATIO = 2.0

DAILY_EMA_PERIOD = 50

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"


# ── DATA FETCH ───────────────────────────────────────────────────────────
def fetch_extended_candles(interval: str, target_bars: int):
    all_rows = []
    end_date = None

    while len(all_rows) < target_bars:
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
            break

        values = data["values"]
        all_rows.extend(values)

        earliest = datetime.strptime(values[-1]["datetime"], "%Y-%m-%d %H:%M:%S" if " " in values[-1]["datetime"] else "%Y-%m-%d")
        step = timedelta(hours=4) if interval == "4h" else timedelta(days=1)
        end_date = (earliest - step).strftime("%Y-%m-%d %H:%M:%S" if interval != "1day" else "%Y-%m-%d")

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
def ema_series(values, period):
    k = 2 / (period + 1)
    out = [values[0]]
    for price in values[1:]:
        out.append(price * k + out[-1] * (1 - k))
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


# ── BUILD DAILY TREND LOOKUP (previous closed day only, no lookahead) ────
def build_daily_trend_lookup(daily_times, daily_closes):
    daily_ema = ema_series(daily_closes, DAILY_EMA_PERIOD)
    # date -> trend state ("UP"/"DOWN"), using THAT day's own close vs EMA
    trend_by_date = {}
    for i in range(len(daily_times)):
        if i < DAILY_EMA_PERIOD:
            continue
        d = daily_times[i].split(" ")[0]
        trend_by_date[d] = "UP" if daily_closes[i] > daily_ema[i] else "DOWN"
    return trend_by_date


def get_prior_day_trend(trend_by_date, sorted_dates, candle_date):
    # Find the most recent daily trend BEFORE candle_date (no lookahead)
    prior = None
    for d in sorted_dates:
        if d >= candle_date:
            break
        prior = d
    return trend_by_date.get(prior) if prior else None


# ── BACKTEST ──────────────────────────────────────────────────────────────
def run_backtest(use_daily_filter: bool):
    times, highs, lows, closes = fetch_extended_candles("4h", TARGET_4H_BARS)
    daily_times, _, _, daily_closes = fetch_extended_candles("1day", 1000)

    trend_by_date = build_daily_trend_lookup(daily_times, daily_closes)
    sorted_dates = sorted(trend_by_date.keys())

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

        if use_daily_filter:
            candle_date = times[i].split(" ")[0]
            daily_trend = get_prior_day_trend(trend_by_date, sorted_dates, candle_date)
            if daily_trend is None:
                continue
            if direction == "BUY" and daily_trend != "UP":
                continue
            if direction == "SELL" and daily_trend != "DOWN":
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


def summarize(label, trades, start_time, end_time):
    total = len(trades)
    wins = trades.count("WIN")
    losses = total - wins
    win_rate = (wins / total * 100) if total else 0
    expectancy_r = (wins * RR_RATIO - losses * 1) / total if total else 0

    print(f"\n=== {label} ===")
    if total == 0:
        print("No signals found.")
    else:
        print(f"Period: {start_time} to {end_time}")
        print(f"Signals: {total}  Win rate: {win_rate:.1f}%  Expectancy: {expectancy_r:.2f}R")


def main():
    print("Testing USD/CHF @ 4H — baseline vs. daily trend confirmation...")

    trades_base, s1, e1 = run_backtest(use_daily_filter=False)
    summarize("BASELINE (no daily filter)", trades_base, s1, e1)

    trades_mtf, s2, e2 = run_backtest(use_daily_filter=True)
    summarize("WITH DAILY TREND CONFIRMATION", trades_mtf, s2, e2)

    print("\nCompare the two directly above. Only adopt the daily filter "
          "live if it clearly improves expectancy WITHOUT collapsing the "
          "sample size to an untrustworthy level.")


if __name__ == "__main__":
    main()
