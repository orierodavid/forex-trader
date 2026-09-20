"""
Forex Signal Bot — Three independent strategies, one bot
-----------------------------------------------------------------------------
1. USD/CHF @ 4H — Support/Resistance Rejection + Daily Trend Confirmation
   Backtested ~5.8 years: 441 signals, 43.5% win rate, +0.31R expectancy

2. XAU/USD @ 4H — Momentum Breakout
   Backtested ~5.8 years: 482 signals, 38.2% win rate, +0.15R expectancy

3. EUR/USD @ 4H — Support/Resistance Rejection (baseline, no daily filter —
   not yet tested with daily confirmation, so not claiming that combo's edge)
   Backtested ~2.7 years: 589 signals, 36.8% win rate, +0.11R expectancy

Each pair uses whichever logic actually tested well for IT specifically —
breakout lost money on USD/CHF, rejection lost money on gold at 1H. These
three are matched to real, separately-verified evidence, not one-size-
fits-all assumptions. Each pair is checked and reported independently;
a signal on one has nothing to do with the others.

IMPORTANT: No strategy guarantees profits. All three are real, backtested
edges — not guesses — but real-world spread/slippage/commission will
reduce actual results. Evaluate every signal yourself before acting.
"""

import requests
from datetime import datetime, timezone

# ── CONFIG (hardcoded — keep this repo PRIVATE) ─────────────────────────
TWELVE_DATA_API_KEY = "dbe551d12fab420d9c5f54c869cab829"
TELEGRAM_BOT_TOKEN   = "8663708941:AAH1U0zCY70VxFMhOwzRWuhUSRrEQ6ZN3bo"
TELEGRAM_CHAT_ID     = "523944035"

INTERVAL_4H = "4h"
CANDLE_COUNT_4H = 500
DAILY_LOOKBACK_DAYS = 120

SWING_LOOKBACK = 5
LEVEL_LOOKBACK_BARS = 300
ATR_PERIOD = 14
RR_RATIO = 2.0

REJECTION_BUFFER_ATR = 0.1
SL_BUFFER_ATR = 0.3
DAILY_EMA_PERIOD = 50

MOMENTUM_MULT = 1.2

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
TELEGRAM_URL = f"https://api.telegram.org/bot{{token}}/sendMessage"


# ── DATA FETCH ───────────────────────────────────────────────────────────
def fetch_candles(pair: str, interval: str, count: int):
    params = {
        "symbol": pair,
        "interval": interval,
        "outputsize": count,
        "apikey": TWELVE_DATA_API_KEY,
        "order": "ASC",
    }
    resp = requests.get(TWELVE_DATA_URL, params=params, timeout=15)
    data = resp.json()
    if "values" not in data:
        raise RuntimeError(f"Twelve Data error for {pair} @ {interval}: {data}")
    candles = data["values"]
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    times = [c["datetime"] for c in candles]
    return times, highs, lows, closes


# ── SHARED INDICATORS ─────────────────────────────────────────────────────
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


# ── GENERIC REJECTION SIGNAL (shared by USD/CHF and EUR/USD) ────────────
def rejection_signal(pair: str, require_daily_confirmation: bool):
    times, highs, lows, closes = fetch_candles(pair, INTERVAL_4H, CANDLE_COUNT_4H)

    min_len = LEVEL_LOOKBACK_BARS + SWING_LOOKBACK * 2 + 10
    if len(closes) < min_len:
        return None

    atr_vals = atr_series(highs, lows, closes, ATR_PERIOD)
    swing_highs, swing_lows = find_swing_points(highs, lows, SWING_LOOKBACK)

    i = len(closes) - 1
    a = atr_vals[i]
    if a is None:
        return None

    window_start = max(0, i - LEVEL_LOOKBACK_BARS)
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
    reason = None

    if resistance is not None:
        if highs[i] >= resistance - buffer_dist and closes[i] < resistance:
            direction = "SELL"
            level_used = resistance
            reason = f"Rejected resistance at {resistance:.5f}"

    if direction is None and support is not None:
        if lows[i] <= support + buffer_dist and closes[i] > support:
            direction = "BUY"
            level_used = support
            reason = f"Bounced off support at {support:.5f}"

    if direction is None:
        return None

    if require_daily_confirmation:
        daily_trend = get_daily_trend(pair, times[i])
        if daily_trend is None:
            return None
        if direction == "BUY" and daily_trend != "UP":
            return None
        if direction == "SELL" and daily_trend != "DOWN":
            return None
        reason += f" (daily trend: {daily_trend}, confirmed)"

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
        return None

    return {
        "pair": pair, "direction": direction,
        "entry": round(price, 5), "sl": round(sl, 5), "tp": round(tp, 5),
        "reason": reason, "time": times[i],
    }


def get_daily_trend(pair: str, current_4h_time: str):
    daily_times, _, _, daily_closes = fetch_candles(pair, "1day", DAILY_LOOKBACK_DAYS)
    daily_ema = ema_series(daily_closes, DAILY_EMA_PERIOD)

    current_date = current_4h_time.split(" ")[0]
    prior_idx = None
    for i, t in enumerate(daily_times):
        d = t.split(" ")[0]
        if d >= current_date:
            break
        prior_idx = i

    if prior_idx is None or prior_idx < DAILY_EMA_PERIOD:
        return None

    return "UP" if daily_closes[prior_idx] > daily_ema[prior_idx] else "DOWN"


def generate_signal_usdchf():
    signal = rejection_signal("USD/CHF", require_daily_confirmation=True)
    if signal:
        signal["strategy"] = "S/R Rejection + Daily Confirmation"
        signal["backtest_note"] = "43.5% win rate, +0.31R over ~5.8yrs"
    return signal


def generate_signal_eurusd():
    signal = rejection_signal("EUR/USD", require_daily_confirmation=False)
    if signal:
        signal["strategy"] = "S/R Rejection (baseline)"
        signal["backtest_note"] = "36.8% win rate, +0.11R over ~2.7yrs"
    return signal


# ── STRATEGY: XAU/USD — Momentum Breakout ────────────────────────────────
def generate_signal_xauusd():
    pair = "XAU/USD"
    times, highs, lows, closes = fetch_candles(pair, INTERVAL_4H, CANDLE_COUNT_4H)

    min_len = LEVEL_LOOKBACK_BARS + SWING_LOOKBACK * 2 + 10
    if len(closes) < min_len:
        return None

    atr_vals = atr_series(highs, lows, closes, ATR_PERIOD)
    swing_highs, swing_lows = find_swing_points(highs, lows, SWING_LOOKBACK)

    i = len(closes) - 1
    a = atr_vals[i]
    if a is None:
        return None

    candle_range = highs[i] - lows[i]
    if candle_range < MOMENTUM_MULT * a:
        return None

    window_start = max(0, i - LEVEL_LOOKBACK_BARS)

    resistance = None
    for idx in sorted([k for k in swing_highs if window_start <= k < i], reverse=True):
        level = swing_highs[idx]
        if level <= closes[i - 1]:
            continue
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
    reason = None

    if resistance is not None and closes[i] > resistance:
        direction = "BUY"
        level_used = resistance
        reason = f"Bullish breakout above resistance {resistance:.2f}"

    if direction is None and support is not None and closes[i] < support:
        direction = "SELL"
        level_used = support
        reason = f"Bearish breakdown below support {support:.2f}"

    if direction is None:
        return None

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
        return None

    return {
        "pair": pair, "strategy": "Momentum Breakout",
        "direction": direction, "entry": round(price, 2),
        "sl": round(sl, 2), "tp": round(tp, 2),
        "reason": reason, "time": times[i],
        "backtest_note": "38.2% win rate, +0.15R over ~5.8yrs",
    }


# ── TELEGRAM DELIVERY ────────────────────────────────────────────────────
def send_telegram_message(text: str):
    url = TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN)
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    resp = requests.post(url, data=payload, timeout=15)
    if resp.status_code != 200:
        print(f"Telegram send failed: {resp.text}")


def format_signal_message(signal: dict) -> str:
    arrow = "🟢" if signal["direction"] == "BUY" else "🔴"
    return (
        f"{arrow} *{signal['direction']} {signal['pair']}*\n"
        f"Strategy: {signal['strategy']}\n"
        f"Entry: `{signal['entry']}`\n"
        f"Stop-Loss: `{signal['sl']}`\n"
        f"Take-Profit: `{signal['tp']}`\n"
        f"Reason: {signal['reason']}\n"
        f"Candle time: {signal['time']}\n\n"
        f"Backtest: {signal['backtest_note']} (before spread/slippage)\n"
        f"⚠️ Not financial advice. Confirm before trading."
    )


# ── MAIN ─────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Checking all three signals...")

    checks = [
        ("USD/CHF (rejection + daily)", generate_signal_usdchf),
        ("XAU/USD (breakout)", generate_signal_xauusd),
        ("EUR/USD (rejection baseline)", generate_signal_eurusd),
    ]

    for label, fn in checks:
        try:
            signal = fn()
            if signal:
                send_telegram_message(format_signal_message(signal))
                print(f"  {label}: signal sent -> {signal['direction']} at {signal['entry']}")
            else:
                print(f"  {label}: no signal this candle")
        except Exception as e:
            print(f"  {label}: ERROR - {e}")


if __name__ == "__main__":
    main()

# ── SCHEDULING ───────────────────────────────────────────────────────────
# All three run on the same 4H schedule — no change needed:
#   cron: "5 0,4,8,12,16,20 * * *"
