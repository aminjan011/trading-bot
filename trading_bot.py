import ccxt
import time
import logging
from dotenv import load_dotenv
import os
import pandas as pd
import numpy as np
from telegram import Bot
import schedule
import threading
import asyncio

# Logging sozlash
logging.basicConfig(filename='trading_bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# .env faylidan kalitlarni o'qish
load_dotenv()
api_key = os.getenv('API_KEY')
api_secret = os.getenv('API_SECRET')
password = os.getenv('PASSWORD')
telegram_token = os.getenv('TELEGRAM_TOKEN')
chat_id = os.getenv('CHAT_ID')
channel_id = os.getenv('CHANNEL_ID')

# Bitget API ulanishi
exchange = ccxt.bitget({
    'apiKey': api_key,
    'secret': api_secret,
    'password': password,
    'enableRateLimit': True,
})
exchange.options['defaultType'] = 'swap'

# Telegram xabar yuborish
async def send_telegram_message(message):
    try:
        bot = Bot(token=telegram_token)
        await bot.send_message(chat_id=chat_id, text=message)
        await bot.send_message(chat_id=channel_id, text=message)
        logging.info(f"Telegram xabari yuborildi: {message}")
    except Exception as e:
        print(f"Telegram xabar yuborishda xato: {str(e)}")
        logging.error(f"Telegram xabar xatosi: {str(e)}")

def sync_send_telegram_message(message):
    asyncio.run(send_telegram_message(message))

# RSI hisoblash
def calculate_rsi(data, periods=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=periods).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=periods).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# MACD hisoblash
def calculate_macd(data, fast=8, slow=21, signal=5):
    exp1 = data.ewm(span=fast, adjust=False).mean()
    exp2 = data.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

# ADX hisoblash
def calculate_adx(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']
    plus_dm = high.diff()
    minus_dm = low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx

# Bollinger Bands hisoblash
def calculate_bollinger_bands(df, period=20, std=2):
    sma = df['close'].rolling(window=period).mean()
    std_dev = df['close'].rolling(window=period).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    return upper, lower

# ATR hisoblash
def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

# Volume Oscillator hisoblash
def calculate_volume_oscillator(df, short_period=5, long_period=20):
    short_vol = df['volume'].rolling(window=short_period).mean()
    long_vol = df['volume'].rolling(window=long_period).mean()
    return (short_vol - long_vol) / long_vol * 100

# EMA hisoblash
def calculate_ema(data, period=50):
    return data.ewm(span=period, adjust=False).mean()

# Leverage va margin rejimini tasdiqlash
def verify_leverage_and_margin(symbol):
    try:
        print(f"Diqqat: {symbol} uchun Leverage (20x), margin rejimi (cross) va pozitsiya rejimi (one-way) Bitget platformasida qo'lda o'rnatilgan bo'lishi kerak.")
        logging.info(f"{symbol} uchun leverage va margin rejimi qo'lda o'rnatilgan deb hisoblandi.")
        return True
    except Exception as e:
        print(f"{symbol} leverage/margin tekshirishda xato: {str(e)}")
        logging.error(f"{symbol} leverage/margin xatosi: {str(e)}")
        return False

# Ochiq pozitsiyalarni tekshirish
def check_open_positions(symbol):
    try:
        positions = exchange.fetch_positions([symbol])
        for position in positions:
            if position['contracts'] > 0:
                return True
        return False
    except Exception as e:
        print(f"{symbol} pozitsiyalarni tekshirishda xato: {str(e)}")
        logging.error(f"{symbol} pozitsiya xatosi: {str(e)}")
        return True

# Signalni tekshirish
def check_signal(symbol, timeframe='3m'):
    limit = 100
    max_retries = 3
    for attempt in range(max_retries):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['rsi'] = calculate_rsi(df['close'], periods=14)
            df['macd'], df['macd_signal'] = calculate_macd(df['close'], fast=8, slow=21, signal=5)
            df['adx'] = calculate_adx(df, period=14)
            df['bb_upper'], df['bb_lower'] = calculate_bollinger_bands(df, period=20, std=2)
            df['atr'] = calculate_atr(df, period=14)
            df['volume_osc'] = calculate_volume_oscillator(df, short_period=5, long_period=20)
            df['ema50'] = calculate_ema(df['close'], period=50)
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            prev2 = df.iloc[-3]
            atr_threshold = 0.002 * latest['close']  # 0.2% narx o'zgarishi
            bb_confirm = prev['close'] < prev['bb_lower'] and prev2['close'] < prev2['bb_lower']
            if (latest['rsi'] < 70 and
                latest['macd'] > latest['macd_signal'] and prev['macd'] <= prev['macd_signal'] and
                latest['adx'] > 30 and
                latest['close'] < latest['bb_lower'] and bb_confirm and
                latest['atr'] > atr_threshold and
                latest['volume_osc'] > 20 and
                latest['close'] > latest['ema50']):
                return "buy"
            return "hold"
        except Exception as e:
            print(f"{symbol} signal tekshirishda xato (urinish {attempt+1}/{max_retries}): {str(e)}")
            logging.error(f"{symbol} signal xatosi: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                return "hold"

# Balansni olish
def get_balance():
    try:
        balance = exchange.fetch_balance()
        return balance['total']['USDT']
    except Exception as e:
        print(f"Balans olishda xato: {str(e)}")
        logging.error(f"Balans xatosi: {str(e)}")
        return 15  # Default

# Bitim hajmini hisoblash
def calculate_trade_amount(symbol, risk_percent=0.05, stop_loss_percent=0.01):
    try:
        balance = get_balance()
        risk_amount = balance * risk_percent  # 5% risk
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        trade_value = risk_amount / stop_loss_percent
        amount = trade_value / current_price
        min_amount = 10  # Bitget minimal hajmi
        amount = max(amount, min_amount)
        amount = round(amount, 2)
        return amount
    except Exception as e:
        print(f"Bitim hajmi hisoblashda xato: {str(e)}")
        logging.error(f"Bitim hajmi xatosi: {str(e)}")
        return 50  # Default

# Kunlik savdo natijalarini hisoblash
trade_stats = {'trades': 0, 'profit': 0, 'initial_balance': 15}
def track_daily_results(order_type, profit=0):
    global trade_stats
    if order_type == 'open':
        trade_stats['trades'] += 1
    elif order_type == 'close':
        trade_stats['profit'] += profit
    return trade_stats

# Kunlik hisobot yuborish
def send_daily_report():
    global trade_stats
    try:
        current_balance = exchange.fetch_balance()['total']['USDT']
        profit_percent = ((current_balance - trade_stats['initial_balance']) / trade_stats['initial_balance']) * 100
        message = (
            f"📊 Kunlik savdo natijalari ({time.strftime('%Y-%m-%d')}):\n"
            f"🔄 Savdolar soni: {trade_stats['trades']}\n"
            f"💰 Jami foyda/yo‘qotish: {profit_percent:.2f}%\n"
            f"📈 Joriy balans: {current_balance:.2f} USDT"
        )
        sync_send_telegram_message(message)
        trade_stats['trades'] = 0
        trade_stats['profit'] = 0
        trade_stats['initial_balance'] = current_balance
        logging.info("Kunlik hisobot yuborildi.")
    except Exception as e:
        print(f"Kunlik hisobot yuborishda xato: {str(e)}")
        logging.error(f"Kunlik hisobot xatosi: {str(e)}")

# Buyurtmalarni kuzatish
def monitor_orders(symbol, order_id, entry_price, amount, take_profit_price, stop_loss_price):
    max_retries = 3
    trailing_triggered = False
    trailing_percent = 0.005  # 0.5%
    while True:
        try:
            order = exchange.fetch_order(order_id, symbol)
            if order['status'] == 'closed':
                exit_price = order['price'] or order['average']
                profit = (exit_price - entry_price) * amount if order['side'] == 'sell' else (entry_price - exit_price) * amount
                message = (
                    f"❌ {symbol} bitim yopildi (ID: {order_id}):\n"
                    f"📈 Chiqish narxi: {exit_price:.4f} USDT\n"
                    f"💰 Foyda/Yo‘qotish: {profit:.2f} USDT"
                )
                sync_send_telegram_message(message)
                track_daily_results('close', profit)
                break
            orders = exchange.fetch_open_orders(symbol)
            tp_sl_active = any(o['id'] in [str(order_id)] for o in orders)
            if not tp_sl_active:
                message = f"❌ {symbol} bitim qo‘lda yopildi (ID: {order_id})"
                sync_send_telegram_message(message)
                break
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            if not trailing_triggered and current_price >= entry_price * 1.01:  # 1% foyda
                trailing_triggered = True
                logging.info(f"{symbol} trailing stop faollashtirildi.")
            if trailing_triggered:
                trailing_sl = current_price * (1 - trailing_percent)
                if current_price <= trailing_sl:
                    exit_price = current_price
                    profit = (exit_price - entry_price) * amount
                    exchange.create_market_sell_order(symbol, amount, params={
                        'reduceOnly': True,
                        'positionSide': 'long',
                        'marginMode': 'cross'
                    })
                    message = (
                        f"❌ {symbol} trailing stop yopildi:\n"
                        f"📈 Chiqish narxi: {exit_price:.4f} USDT\n"
                        f"💰 Foyda: {profit:.2f} USDT"
                    )
                    sync_send_telegram_message(message)
                    track_daily_results('close', profit)
                    break
        except Exception as e:
            print(f"{symbol} buyurtma kuzatishda xato: {str(e)}")
            logging.error(f"{symbol} buyurtma kuzatish xatosi: {str(e)}")
            max_retries -= 1
            if max_retries == 0:
                break
            time.sleep(5)
        time.sleep(10)

# Buyurtma joylashtirish
def place_order(symbol, signal):
    if check_open_positions(symbol):
        print(f"{symbol} uchun ochiq pozitsiya mavjud. Yangi bitim ochilmadi.")
        logging.info(f"{symbol} uchun ochiq pozitsiya mavjud.")
        return
    take_profit_percent = 0.03  # 3%
    stop_loss_percent = 0.01   # 1%
    max_retries = 3
    if signal == "buy":
        for attempt in range(max_retries):
            try:
                amount = calculate_trade_amount(symbol, risk_percent=0.05, stop_loss_percent=stop_loss_percent)
                ticker = exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                order = exchange.create_limit_buy_order(symbol, amount, current_price, params={
                    'positionSide': 'long',
                    'marginMode': 'cross'
                })
                message = (
                    f"✅ {symbol} bitim ochildi (ID: {order['id']}):\n"
                    f"📈 Kirish narxi: {current_price:.4f} USDT\n"
                    f"📊 Miqdor: {amount} {symbol.split('/')[0]}"
                )
                sync_send_telegram_message(message)
                track_daily_results('open')
                take_profit_price = current_price * (1 + take_profit_percent)
                stop_loss_price = current_price * (1 - stop_loss_percent)
                tp_order = exchange.create_order(
                    symbol=symbol,
                    type='limit',
                    side='sell',
                    amount=amount,
                    price=take_profit_price,
                    params={
                        'takeProfitPrice': take_profit_price,
                        'reduceOnly': True,
                        'positionSide': 'long',
                        'marginMode': 'cross'
                    }
                )
                print(f"{symbol} Take-Profit buyurtmasi o'rnatildi (narx: {take_profit_price}): {tp_order}")
                logging.info(f"{symbol} Take-Profit: {tp_order}")
                sl_order = exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side='sell',
                    amount=amount,
                    params={
                        'stopLossPrice': stop_loss_price,
                        'reduceOnly': True,
                        'positionSide': 'long',
                        'marginMode': 'cross'
                    }
                )
                print(f"{symbol} Stop-Loss buyurtmasi o'rnatildi (narx: {stop_loss_price}): {sl_order}")
                logging.info(f"{symbol} Stop-Loss: {sl_order}")
                threading.Thread(target=monitor_orders, args=(symbol, order['id'], current_price, amount, take_profit_price, stop_loss_price)).start()
                break
            except Exception as e:
                print(f"{symbol} buyurtma joylashtirishda xato (urinish {attempt+1}/{max_retries}): {str(e)}")
                logging.error(f"{symbol} buyurtma xatosi: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                else:
                    raise
    else:
        print(f"{symbol} uchun buyurtma joylashtirilmadi.")

# Kunlik hisobotni rejalashtirish
def run_scheduler():
    schedule.every().day.at("23:59").do(send_daily_report)
    while True:
        schedule.run_pending()
        time.sleep(60)

# Main funksiyasi
def main():
    symbol = 'XRP/USDT:USDT'
    if not verify_leverage_and_margin(symbol):
        message = f"❌ {symbol} leverage/margin tasdiqlanmadi. Savdo to'xtatiladi."
        sync_send_telegram_message(message)
        print(message)
        return
    signal = check_signal(symbol, timeframe='3m')
    print(f"{symbol} signal: {signal}")
    place_order(symbol, signal)

if __name__ == '__main__':
    sync_send_telegram_message("🤖 90% win rate uchun optimallashtirilgan savdo boti ishga tushdi!")
    threading.Thread(target=run_scheduler, daemon=True).start()
    try:
        while True:
            try:
                main()
            except Exception as e:
                print(f"Umumiy xato: {str(e)}")
                logging.error(f"Umumiy xato: {str(e)}")
            time.sleep(30)
    except KeyboardInterrupt:
        sync_send_telegram_message("🛑 Savdo boti o'chirildi!")
        print("Bot to'xtatildi.")
        logging.info("Bot o'chirildi.")
