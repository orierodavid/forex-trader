# Forex Signal Bot — EUR/USD & USD/JPY

A simple rules-based signal bot that checks EUR/USD and USD/JPY on the 1-hour
timeframe and sends buy/sell signals to a Telegram chat.

**This bot does not guarantee profitable trades.** No signal system can. It
runs a defined trend + RSI pullback strategy with ATR-based stop-loss and
take-profit — you should evaluate every signal yourself before acting on it.

## ⚠️ Before you push this repo

Your Twelve Data API key and Telegram bot token are hardcoded directly in
`forex_signal_bot.py`. **Keep this GitHub repository PRIVATE.** Anyone who
can read this file can:
- Send messages through your Telegram bot
- Use your Twelve Data API quota

If you ever suspect either credential has leaked, regenerate them immediately:
- Twelve Data: regenerate your key from your Twelve Data dashboard
- Telegram: message @BotFather → `/revoke` (or `/token` to get a new one) for your bot

## Files

- `forex_signal_bot.py` — the bot itself
- `requirements.txt` — Python dependencies
- `.github/workflows/signal_bot.yml` — runs the bot automatically every hour via GitHub Actions

## Setup

1. **Get your Telegram chat ID** (if you haven't already):
   - Message your bot anything in Telegram first
   - Visit `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in a browser
   - Find `"chat":{"id": ...}` in the response — that's your chat ID
   - Paste it into `TELEGRAM_CHAT_ID` in `forex_signal_bot.py`

2. **Test locally (optional but recommended):**
   ```bash
   pip install -r requirements.txt
   python3 forex_signal_bot.py
   ```
   You should see console output for each pair, and a Telegram message if a
   signal triggered.

3. **Push to GitHub:**
   - Create a **private** repository
   - Add these files, keeping the folder structure:
     ```
     your-repo/
     ├── forex_signal_bot.py
     ├── requirements.txt
     └── .github/
         └── workflows/
             └── signal_bot.yml
     ```
   - Push from your local machine as usual

4. **Confirm it's running:**
   - Go to the **Actions** tab on GitHub
   - Click **Forex Signal Bot** → **Run workflow** to trigger it manually and check the logs
   - After that, it runs automatically every hour on its own (via the cron schedule in the workflow file)

## Strategy summary

- **Trend filter:** 50 EMA vs 200 EMA
- **Trigger:** RSI(14) pullback in the trend direction (RSI < 45 in an uptrend for BUY, RSI > 55 in a downtrend for SELL)
- **Stop-loss:** 1.5× ATR(14) from entry
- **Take-profit:** 3.0× ATR(14) from entry (1:2 risk-reward)

## Recommended next step

Log every signal this sends and manually track whether it hits TP or SL.
After 30–50 signals you'll have real data on whether this strategy suits
your risk tolerance — not just a hunch.
