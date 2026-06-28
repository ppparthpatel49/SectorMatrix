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
    if not os.path.exists(filename): return {}
    with open(filename, 'r') as f: return json.load(f)

def load_portfolio(filename="portfolio.csv"):
    if not os.path.exists(filename): return []
    try:
        df = pd.read_csv(filename, header=None)
        return df[0].dropna().astype(str).str.strip().tolist()
    except: return []

# Move to the script's directory so it finds sectors.json correctly when run by GitHub Actions
os.chdir(os.path.dirname(os.path.abspath(__file__)))

sectors = load_sectors()
portfolio = load_portfolio()
if not sectors:
    print("sectors.json not found!")
    exit()

def get_sector_for_ticker(ticker):
    for sec, stocks in sectors.items():
        if ticker in stocks: return sec
    return None

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def check_fundamentals(ticker):
    try:
        info = yf.Ticker(ticker).info
        roe = info.get('returnOnEquity', 0)
        eg = info.get('earningsQuarterlyGrowth', 0)
        if roe is None: roe = 0
        if eg is None: eg = 0
        return (roe > 0.15) or (eg > 0.10)
    except:
        return True 

# ==========================================
# UPGRADE 1: MACRO ENVIRONMENT CHECK
# ==========================================
print("Checking Macro Environment (Nifty & VIX)...")
tv = TvDatafeed()

kill_switch_active = False
macro_status = ""

try:
    df_nifty = tv.get_hist('NIFTY', 'NSE', Interval.in_daily, 120)
    df_vix = tv.get_hist('INDIAVIX', 'NSE', Interval.in_daily, 10)
    
    nifty_close = df_nifty['close'].iloc[-1]
    nifty_100_sma = df_nifty['close'].rolling(100).mean().iloc[-1]
    vix_close = df_vix['close'].iloc[-1]
    
    if nifty_close < nifty_100_sma:
        kill_switch_active = True
        macro_status = f"🚨 *MACRO WARNING:* Nifty is below 100-DMA (Correction)."
    elif vix_close > 22:
        kill_switch_active = True
        macro_status = f"🚨 *MACRO WARNING:* India VIX is extremely high ({vix_close:.1f})."
    else:
        macro_status = f"🟢 *Macro Check:* Nifty is Trending & VIX is healthy ({vix_close:.1f})."
except Exception as e:
    macro_status = f"⚠️ *Macro Check Failed:* Proceeding with caution."

# ==========================================
# STEP A: TRUE SECTOR SCORING 
# ==========================================
print("Scoring Sectors...")
sector_scores = {}
for sec in sectors.keys():
    try:
        df = tv.get_hist(symbol=sec, exchange='NSE', interval=Interval.in_daily, n_bars=100)
        if df is None or len(df) < 65: continue
        c = df['close']
        score = (((c.iloc[-1] - c.iloc[-20]) / c.iloc[-20]) * 0.5) + \
                (((c.iloc[-1] - c.iloc[-60]) / c.iloc[-60]) * 0.3) + \
                (((c.iloc[-1] - c.iloc[-5]) / c.iloc[-5]) * 0.2)
        sector_scores[sec] = score * 100
        time.sleep(1)
    except: continue

if not sector_scores:
    send_telegram("⚠️ Matrix Engine Error: Could not fetch Sector data.")
    exit()

sorted_sectors = sorted(sector_scores.keys(), key=lambda x: sector_scores[x], reverse=True)
best_sec = sorted_sectors[0]
top_3_sectors = sorted_sectors[:3]
best_sec_friendly = best_sec.replace('CNX', '').replace('BANKNIFTY', 'BANK')

# ==========================================
# UPGRADE 4: SECTOR ROTATION EXITS
# ==========================================
print("Checking Portfolio for Sector Rotation...")
rotation_warnings = []
for p_stock in portfolio:
    s = get_sector_for_ticker(p_stock)
    if s and s not in top_3_sectors:
        rank = sorted_sectors.index(s) + 1 if s in sorted_sectors else "Unknown"
        s_friendly = s.replace('CNX', '').replace('BANKNIFTY', 'BANK')
        rotation_warnings.append(f"📉 *{p_stock.replace('.NS','')}*: Sector ({s_friendly}) dropped to Rank #{rank}. Time to Rotate Out!")

# ==========================================
# STEP B: SCAN THE APEX SECTOR 
# ==========================================
all_sector_stocks = []
strong_stocks = []
pullback_triggers = []

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

print(f"Scanning stocks in {best_sec_friendly}...")
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
        
        c, l, o, v = get_val(df, 'Close'), get_val(df, 'Low'), get_val(df, 'Open'), get_val(df, 'Volume')
        rsi_val = get_val(df, 'RSI')
        atr = get_val(df, 'ATR')
        
        if pd.isna(rsi_val):
            rsi_val = 0.0

        all_sector_stocks.append({"ticker": stock.replace('.NS', ''), "rsi": float(rsi_val)})
        
        is_liquid = (c * v) > 100000000
        is_uptrend = c > get_val(df, 'SMA_50') and get_val(df, 'SMA_50') > get_val(df, 'SMA_200')
        
        if is_liquid and is_uptrend and rsi_val > 60:
            if check_fundamentals(stock):
                strong_stocks.append({"ticker": stock.replace('.NS', ''), "price": c, "rsi": float(rsi_val)})
                
                if l <= get_val(df, 'EMA_21') and c > o and v < get_val(df, 'Vol_SMA'):
                    stop_loss = l - (atr * 0.5)
                    shares = int((ACCOUNT_SIZE * RISK_PER_TRADE) / (c - stop_loss)) if (c - stop_loss) > 0 else 0
                    pullback_triggers.append({"ticker": stock.replace('.NS', ''), "entry": c, "stop": stop_loss, "shares": shares})
    except Exception as e:
        continue

all_sector_stocks = sorted(all_sector_stocks, key=lambda k: k['rsi'], reverse=True)
strong_stocks = sorted(strong_stocks, key=lambda k: k['rsi'], reverse=True)

top_3_leaders = all_sector_stocks[:3]

# ==========================================
# UPGRADE 3: AUTOMATED CSV JOURNALING
# ==========================================
date_today = datetime.datetime.now().strftime("%Y-%m-%d")
if pullback_triggers and not kill_switch_active:
    file_exists = os.path.isfile('trade_journal.csv')
    with open('trade_journal.csv', 'a') as f:
        if not file_exists:
            f.write("Date,Ticker,Sector,EntryPrice,StopLoss,Quantity\n")
        for t in pullback_triggers:
            f.write(f"{date_today},{t['ticker']},{best_sec_friendly},{t['entry']:.2f},{t['stop']:.2f},{t['shares']}\n")

# ==========================================
# STEP C: FIRE ALERTS
# ==========================================
msg = f" *SECTOR MATRIX* ({date_today})\n"
msg += f"{macro_status}\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n\n"

if rotation_warnings:
    msg += f"⚠️ *SECTOR ROTATION WARNINGS*\n"
    for w in rotation_warnings: msg += f"{w}\n"
    msg += f"\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n\n"

msg += f"🏆 *#1 Sector:* {best_sec_friendly}\n"
if top_3_leaders:
    leaders_str = ", ".join([f"{s['ticker']} ({s['rsi']:.0f})" for s in top_3_leaders])
    msg += f"🥇 *Top 3 Leaders:* {leaders_str}\n\n"

if pullback_triggers:
    if kill_switch_active:
        msg += f"🎯 *FUNDAMENTAL 21-EMA PULLBACKS (PAPER TRADE)*\n"
        msg += f"_Macro environment is red. Deploying capital is dangerous._\n"
    else:
        msg += f"🎯 *FUNDAMENTAL 21-EMA PULLBACKS (BUY)*\n"
        
    for t in pullback_triggers:
        msg += f"🚀 *{t['ticker']}*\n🔹 Entry: ₹{t['entry']:.2f} | 🔴 Stop: ₹{t['stop']:.2f} | 📦 Qty: {t['shares']}\n\n"
else:
    msg += f"🎯 *FUNDAMENTAL 21-EMA PULLBACKS:*\n⚠️ None triggered today. Wait for a dip.\n\n"

if strong_stocks:
    printed_any = False
    temp_msg = f"💪 *STRONG FUNDAMENTAL RADAR (RSI>60)*\n"
    for s in strong_stocks[:5]:
        if not any(t['ticker'] == s['ticker'] for t in pullback_triggers):
            temp_msg += f"• {s['ticker']} (RSI: {s['rsi']:.0f})\n"
            printed_any = True
    if printed_any:
        msg += temp_msg

send_telegram(msg)
print("Finished! Matrix execution complete.")
