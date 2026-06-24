import os
import yfinance as yf
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

sectors = {
    "^CNXAUTO": ["TATAMOTORS.NS", "MARUTI.NS", "M&M.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS", "TVSMOTOR.NS", "HEROMOTOCO.NS", "ASHOKLEY.NS"],
    "^CNXIT": ["TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "LTIM.NS", "PERSISTENT.NS", "COFORGE.NS"],
    "^CNXREALTY": ["DLF.NS", "GODREJPROP.NS", "OBEROIRLTY.NS", "PRESTIGE.NS", "PHOENIXLTD.NS", "LODHA.NS"],
    "^CNXMETAL": ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "JINDALSTEL.NS", "COALINDIA.NS"],
    "^CNXPHARMA": ["SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS", "DIVISLAB.NS", "LUPIN.NS", "AUROPHARMA.NS", "TORNTPHARM.NS"],
    "^CNXPSUBANK": ["SBIN.NS", "BOB.NS", "PNB.NS", "CANBK.NS", "UNIONBANK.NS", "INDIANB.NS"],
    "^CNXENERGY": ["RELIANCE.NS", "ONGC.NS", "NTPC.NS", "POWERGRID.NS", "TATAPOWER.NS", "IOC.NS"]
}

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

sector_scores = {}
for sec_ticker in sectors.keys():
    try:
        data = yf.download(sec_ticker, period="6mo", progress=False)
        if len(data) < 65: continue
        if isinstance(data.columns, pd.MultiIndex): data.columns = [c[0] for c in data.columns]
        c = data['Close']
        score = (((c.iloc[-1] - c.iloc[-20]) / c.iloc[-20]) * 0.5) + (((c.iloc[-1] - c.iloc[-60]) / c.iloc[-60]) * 0.3) + (((c.iloc[-1] - c.iloc[-5]) / c.iloc[-5]) * 0.2)
        sector_scores[sec_ticker] = score * 100
    except: continue

if not sector_scores:
    send_telegram("⚠️ Matrix Engine Error: Could not fetch sector data today.")
    exit()

best_sec = max(sector_scores, key=sector_scores.get)
best_sec_name = best_sec.replace('^CNX', '')

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

        if (get_val(df, 'Turnover') > 500000000 and c > get_val(df, 'SMA_50') > get_val(df, 'SMA_200') and 
            get_val(df, 'RSI') > 60 and l <= get_val(df, 'EMA_21') and c > o and v < get_val(df, 'Vol_SMA')):
            
            stop_loss = l - (atr * 0.5)
            shares = int((ACCOUNT_SIZE * RISK_PER_TRADE) / (c - stop_loss)) if (c - stop_loss) > 0 else 0
            triggered.append({"ticker": stock.replace('.NS', ''), "entry": c, "stop": stop_loss, "shares": shares})
    except: continue

date_today = datetime.datetime.now().strftime("%Y-%m-%d")
if triggered:
    msg = f"🟩 *ROBUST MATRIX ALGORITHM* ({date_today})\n\n👑 *Dominant Sector:* {best_sec_name}\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n"
    for t in triggered:
        msg += f"🚀 *{t['ticker']}* (VCP Pullback)\n🔹 Entry: ₹{t['entry']:.2f}\n🔴 Stop: ₹{t['stop']:.2f}\n📦 Qty: {t['shares']} shares\n\n"
else:
    msg = f"⬛️ *ROBUST MATRIX ALGORITHM* ({date_today})\n\n👑 *Dominant Sector:* {best_sec_name}\n⚠️ No valid pullback setups today."

send_telegram(msg)
