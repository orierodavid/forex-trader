"""
Forex Signal Bot — EUR/USD & USD/JPY (1H timeframe)
----------------------------------------------------
Pulls candles from Twelve Data, checks a trend + RSI pullback strategy,
and sends signals to a Telegram chat.

STRATEGY (rules-based, NOT a guarantee of profit):
  - Trend filter : 50 EMA vs 200 EMA
  - Trigger      : RSI(14) pulls back in the direction of the trend
                     * Uptrend (EMA50 > EMA200)  and RSI < 45  -> BUY
                     * Downtrend (EMA50 < EMA200) and RSI > 55 -> SELL
  - Stop-loss    : 1.5x ATR(14) from entry
  - Take-profit  : 3.0x ATR(14) from entry  (fixed 1:2 risk-reward)

NOTE ON SECURITY: This version has credentials hardcoded directly below,
as requested for a personal project. Keep this repo PRIVATE on GitHub —
anyone who can read this file can use your Telegram bot and Twelve Data key.

IMPORTANT: This bot generates signals for you to evaluate — it does not
place trades. Always confirm signals against your own judgment and never
risk more than you can afford to lose. No strategy guarantees profits.
"""

import requests
from datetime import datetime, timezone

# ── CONFIG (hardcoded — keep this repo PRIVATE) ─────────────────────────
TWELVE_DATA_API_KEY = "dbe551d12fab420d9c5f54c869cab829"
TELEGRAM_BOT_TOKEN   = "8663708941:AAH1U0zCY70VxFMhOwzRWuhUSRrEQ6ZN3bo"
TELEGRAM_CHAT_ID     = "523944035"

PAIRS = ["EUR/USD", "USD/JPY"]
INTERVAL = "1h"        # candle timeframe
CANDLE_COUNT = 250      # need 200+ for the 200 EMA

EMA_FAST = 50
EMA_SLOW = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 3.0       # 1:2 risk-reward vs the SL multiple above

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
TELEGRAM_URL = f"https://api.telegram.org/bot{{token}}/sendMessage"


# ── DATA FETCH ───────────────────────────────────────────────────────────
def fetch_candles(pair: str):
    params = {
        "symbol": pair,
        "interval": INTERVAL,
        "outputsize": CANDLE_COUNT,
        "apikey": TWELVE_DATA_API_KEY,
        "order": "ASC",
    }
    resp = requests.get(TWELVE_DATA_URL, params=params, timeout=15)
    data = resp.json()
    if "values" not in data:
        raise RuntimeError(f"Twelve Data error for {pair}: {data}")
    candles = data["values"]
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    times = [c["datetime"] for c in candles]
    return times, highs, lows, closes


# ── INDICATORS (pure python, no external TA library needed) ────────────────
def ema(values, period):
    k = 2 / (period + 1)
    ema_vals = [values[0]]
    for price in values[1:]:
        ema_vals.append(price * k + ema_vals[-1] * (1 - k))
    return ema_vals


def rsi(values, period):
    gains, losses = [0], [0]
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    rsi_vals = [None] * period

    for i in range(period, len(values)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        rsi_vals.append(100 - (100 / (1 + rs)))
    return rsi_vals


def atr(highs, lows, closes, period):
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    atr_vals = [None] * (period - 1)
    atr_vals.append(sum(trs[:period]) / period)
    for i in range(period, len(trs)):
        atr_vals.append((atr_vals[-1] * (period - 1) + trs[i]) / period)
    return atr_vals


# ── STRATEGY ─────────────────────────────────────────────────────────────
def generate_signal(pair: str):
    times, highs, lows, closes = fetch_candles(pair)

    if len(closes) < EMA_SLOW + 5:
        return None  # not enough data yet

    ema_fast_vals = ema(closes, EMA_FAST)
    ema_slow_vals = ema(closes, EMA_SLOW)
    rsi_vals = rsi(closes, RSI_PERIOD)
    atr_vals = atr(highs, lows, closes, ATR_PERIOD)

    price = closes[-1]
    fast = ema_fast_vals[-1]
    slow = ema_slow_vals[-1]
    current_rsi = rsi_vals[-1]
    current_atr = atr_vals[-1]
    prev_rsi = rsi_vals[-2] if len(rsi_vals) >= 2 else None

    if current_rsi is None or current_atr is None or prev_rsi is None:
        return None

    uptrend = fast > slow
    downtrend = fast < slow

    direction = None
    reason = None

    # Only fire on the candle RSI FIRST crosses the threshold, not every
    # candle it happens to stay past it (otherwise one pullback sends you
    # the same signal over and over, hour after hour).
    if uptrend and current_rsi < 45 and prev_rsi >= 45:
        direction = "BUY"
        reason = f"Uptrend (EMA{EMA_FAST}>EMA{EMA_SLOW}) + RSI just crossed into pullback ({current_rsi:.1f})"
    elif downtrend and current_rsi > 55 and prev_rsi <= 55:
        direction = "SELL"
        reason = f"Downtrend (EMA{EMA_FAST}<EMA{EMA_SLOW}) + RSI just crossed into pullback ({current_rsi:.1f})"

    if direction is None:
        return None

    if direction == "BUY":
        sl = price - ATR_SL_MULT * current_atr
        tp = price + ATR_TP_MULT * current_atr
    else:
        sl = price + ATR_SL_MULT * current_atr
        tp = price - ATR_TP_MULT * current_atr

    return {
        "pair": pair,
        "direction": direction,
        "entry": round(price, 5),
        "sl": round(sl, 5),
        "tp": round(tp, 5),
        "reason": reason,
        "time": times[-1],
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
        f"Entry: `{signal['entry']}`\n"
        f"Stop-Loss: `{signal['sl']}`\n"
        f"Take-Profit: `{signal['tp']}`\n"
        f"Reason: {signal['reason']}\n"
        f"Candle time: {signal['time']}\n\n"
        f"⚠️ Not financial advice. Confirm before trading and use proper position sizing."
    )


# ── MAIN ─────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Checking signals...")
    for pair in PAIRS:
        try:
            signal = generate_signal(pair)
            if signal:
                msg = format_signal_message(signal)
                send_telegram_message(msg)
                print(f"  {pair}: signal sent -> {signal['direction']}")
            else:
                print(f"  {pair}: no signal this candle")
        except Exception as e:
            print(f"  {pair}: ERROR - {e}")


if __name__ == "__main__":
    main()

# ── SCHEDULING ───────────────────────────────────────────────────────────
# Run this every hour on the hour so it checks each new 1H candle close.
# Easiest options:
#   - Linux/Mac cron:  0 * * * * /usr/bin/python3 /path/to/forex_signal_bot.py
#   - Or wrap main() in a loop with time.sleep() and a scheduler like `schedule`
#   - Or use the included GitHub Actions workflow (signal_bot.yml)
