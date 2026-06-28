import os
import time
import json
import yfinance as yf
from tvDatafeed import TvDatafeed, Interval
import pandas as pd
import numpy as np
import requests
import datetime
import warnings

warnings.filterwarnings('ignore')

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
ACCOUNT_SIZE = 100000  
RISK_PER_TRADE = 0.01  

def load_sectors(filename="sectors.json"):
    if not os.path.exists(filename):
        print(f"Error: {filename} missing!")
        return {}
    with open(filename, 'r') as f:
        return json.load(f)

sectors = load_sectors()

if not sectors:
    exit()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def calc_atr(df, period=14):
    ranges = pd.concat([df['High'] - df['Low'], np.abs(df['High'] - df['Close'].shift()), np.abs(df['Low'] - df['Close'].shift())], axis=1)
    return ranges.max(axis=1).rolling(period).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    return 100 - (100 / (1 + (gain / loss)))

def get_val(df, col, idx=-1):
    val = df[col].iloc[idx]
    return float(val.iloc[0]) if isinstance(val, pd.Series) else float(val)

# ==========================================
# STEP A: TRUE SECTOR SCORING (TradingView)
# ==========================================
print(f"Fetching official NSE Sector data for {len(sectors)} sectors from TradingView...")
tv = TvDatafeed()
sector_scores = {}

for sec in sectors.keys():
    try:
        # Download exactly 100 days of the official sector index
        df = tv.get_hist(symbol=sec, exchange='NSE', interval=Interval.in_daily, n_bars=100)
        if df is None or len(df) < 65: continue
        
        c = df['close']
        score = (((c.iloc[-1] - c.iloc[-20]) / c.iloc[-20]) * 0.5) + \
                (((c.iloc[-1] - c.iloc[-60]) / c.iloc[-60]) * 0.3) + \
                (((c.iloc[-1] - c.iloc[-5]) / c.iloc[-5]) * 0.2)
                
        sector_scores[sec] = score * 100
        time.sleep(1)  # Avoid rate limits
    except Exception as e:
        print(f"Failed to fetch {sec}: {e}")
        continue

if not sector_scores:
    send_telegram("⚠️ Matrix Engine Error: Could not fetch TradingView Sector data today.")
    exit()

best_sec = max(sector_scores, key=sector_scores.get)
best_sec_friendly = best_sec.replace('CNX', '').replace('BANKNIFTY', 'BANK')
print(f"Top Sector: {best_sec_friendly} (Score: {sector_scores[best_sec]:.2f})")

# ==========================================
# STEP B: SCAN THE APEX SECTOR (Yahoo Finance)
# ==========================================
print(f"Scanning {len(sectors[best_sec])} constituent stocks via Yahoo Finance...")
triggered = []

for stock in sectors[best_sec]:
    try:
        df = yf.download(stock, period="1y", progress=False)
        if len(df) < 200: continue
        if isinstance(df.columns, pd.MultiIndex): df.columns = [c[0] for c in df.columns]
        
        df['SMA_50'] = df['Close'].rolling(50).mean()
        df['SMA_200'] = df['Close'].rolling(200).mean()
        df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['Vol_SMA'] = df['Volume'].rolling(20).mean()
        df['RSI'] = calc_rsi(df['Close'])
        df['ATR'] = calc_atr(df)
        df['Turnover'] = df['Close'] * df['Volume']
        
        c, l, o, v, atr = get_val(df, 'Close'), get_val(df, 'Low'), get_val(df, 'Open'), get_val(df, 'Volume'), get_val(df, 'ATR')

        if (get_val(df, 'Turnover') > 100000000 and c > get_val(df, 'SMA_50') > get_val(df, 'SMA_200') and 
            get_val(df, 'RSI') > 60 and l <= get_val(df, 'EMA_21') and c > o and v < get_val(df, 'Vol_SMA')):
            
            stop_loss = l - (atr * 0.5)
            shares = int((ACCOUNT_SIZE * RISK_PER_TRADE) / (c - stop_loss)) if (c - stop_loss) > 0 else 0
            triggered.append({"ticker": stock.replace('.NS', ''), "entry": c, "stop": stop_loss, "shares": shares})
    except: continue

# ==========================================
# STEP C: FIRE ALERTS
# ==========================================
date_today = datetime.datetime.now().strftime("%Y-%m-%d")
if triggered:
    msg = f"🟩 *ROBUST MATRIX ALGORITHM* ({date_today})\n\n👑 *Dominant Sector:* {best_sec_friendly}\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
    for t in triggered:
        msg += f"🚀 *{t['ticker']}* (VCP Pullback)\n🔹 Entry: ₹{t['entry']:.2f}\n🔴 Stop: ₹{t['stop']:.2f}\n📦 Qty: {t['shares']} shares\n\n"
else:
    msg = f"⬛️ *ROBUST MATRIX ALGORITHM* ({date_today})\n\n👑 *Dominant Sector:* {best_sec_friendly}\n⚠️ No valid pullback setups today."

send_telegram(msg)
print("Finished!")
