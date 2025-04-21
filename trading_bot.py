import ccxt
import pandas as pd
import pandas_ta as ta
import time
import schedule
from telegram.ext import Application, CommandHandler
from dotenv import load_dotenv
import os
import logging

# Logging sozlamalari
logging.basicConfig(
    filename='trading_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# .env faylidan o'qish
load_dotenv()
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('API_SECRET')
CHAT_ID = os.getenv('CHAT_ID')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
PASSWORD = os.getenv('PASSWORD')

# Bitget birjasi sozlamalari
exchange = ccxt.bitget({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})
exchange.set_sandbox_mode(False)  # Real hisob uchun False

# Telegram bot sozlamalari
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Indikator sozlamalari
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 60
MACD_FAST = 8
MACD_SLOW = 21
MACD_SIGNAL = 5
ADX_THRESHOLD = 20
EMA_PERIOD = 10

# Komissiyalar
MAKER_FEE = 0.0002  # 0.02%
TAKER_FEE = 0.0006  # 0.06%
FUNDING_RATE = 0.0002  # Taxminiy 0.02% (24 soat uchun 3 marta)
TOTAL_FEES = MAKER_FEE + TAKER_FEE + FUNDING_RATE  # 0.14%

# TP va SL sozlamalari (komissiyalarni hisobga olgan holda)
STOP_LOSS_PERCENT = 0.006  # 0.6%
TAKE_PROFIT_PERCENT = 0.017  # 1.7%
EFFECTIVE_TP = TAKE_PROFIT_PERCENT - TOTAL_FEES
EFFECTIVE_SL = STOP_LOSS_PERCENT + TOTAL_FEES
RISK_PER_TRADE = 0.01  # Balansning 1% riski

# Global o'zgaruvchilar
SYMBOL = 'XRP/USDT:USDT'
TIMEFRAME = '1h'
DAILY_PROFIT_TARGET = 0.01  # 1%
initial_balance = 0
current_balance = 0
daily_profit = 0

# Ma'lumotlarni olish
def fetch_data():
    ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=100)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# Indikatorlarni hisoblash
def calculate_indicators(df):
    df['ema10'] = ta.ema(df['close'], length=EMA_PERIOD)
    df['rsi'] = ta.rsi(df['close'], length=14)
    macd = ta.macd(df['close'], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    df['macd'] = macd['MACD_8_21_5']
    df['macd_signal'] = macd['MACDs_8_21_5']
    df['adx'] = ta.adx(df['high'], df['low'], df['close'], length=14)['ADX_14']
    return df

# Signal hosil qilish
def generate_signal(data):
    rsi = data['rsi'].iloc[-1]
    macd = data['macd'].iloc[-1]
    macd_signal = data['macd_signal'].iloc[-1]
    adx = data['adx'].iloc[-1]
    ema10 = data['ema10'].iloc[-1]
    close = data['close'].iloc[-1]

    buy_signal = (rsi < RSI_OVERSOLD) and (macd > macd_signal) and (adx > ADX_THRESHOLD) and (close > ema10)
    sell_signal = (rsi > RSI_OVERBOUGHT) and (macd < macd_signal) and (adx > ADX_THRESHOLD) and (close < ema10)

    return buy_signal, sell_signal

# Balansni olish
def get_balance():
    balance = exchange.fetch_balance()
    usdt_balance = balance['total'].get('USDT', 0)
    return usdt_balance

# Position hajmini hisoblash
def calculate_position_size(balance, price):
    max_loss = balance * RISK_PER_TRADE
    price_diff = price * STOP_LOSS_PERCENT
    position_size = max_loss / price_diff
    return position_size

# Savdo ochish
async def open_trade(side, price, balance):
    try:
        amount = calculate_position_size(balance, price)
        sl_price = price * (1 - STOP_LOSS_PERCENT) if side == "buy" else price * (1 + STOP_LOSS_PERCENT)
        tp_price = price * (1 + TAKE_PROFIT_PERCENT) if side == "buy" else price * (1 - TAKE_PROFIT_PERCENT)

        order = exchange.create_order(
            SYMBOL, 'limit', side, amount, price,
            params={'stopLossPrice': sl_price, 'takeProfitPrice': tp_price}
        )
        logging.info(f"{side.capitalize()} order placed: {order}")
        await application.bot.send_message(chat_id=CHAT_ID, text=f"{side.capitalize()} order placed at {price} with SL: {sl_price}, TP: {tp_price}")
    except Exception as e:
        logging.error(f"Error placing {side} order: {e}")
        await application.bot.send_message(chat_id=CHAT_ID, text=f"Error placing {side} order: {e}")

# Botni boshqarish
async def trade():
    global initial_balance, current_balance, daily_profit
    try:
        # Balansni yangilash
        current_balance = get_balance()
        if initial_balance == 0:
            initial_balance = current_balance
        daily_profit = (current_balance - initial_balance) / initial_balance

        # Kunlik foyda maqsadiga yetildi
        if daily_profit >= DAILY_PROFIT_TARGET:
            logging.info(f"Daily profit target reached: {daily_profit*100}%")
            await application.bot.send_message(chat_id=CHAT_ID, text=f"Daily profit target reached: {daily_profit*100}%")
            return

        # Ma'lumotlarni olish va indikatorlarni hisoblash
        df = fetch_data()
        df = calculate_indicators(df)
        buy_signal, sell_signal = generate_signal(df)

        # Narxni olish
        ticker = exchange.fetch_ticker(SYMBOL)
        current_price = ticker['last']

        # Signal asosida savdo
        if buy_signal:
            logging.info(f"Buy signal generated for {SYMBOL}")
            await application.bot.send_message(chat_id=CHAT_ID, text=f"Buy signal generated for {SYMBOL}")
            await open_trade('buy', current_price, current_balance)
        elif sell_signal:
            logging.info(f"Sell signal generated for {SYMBOL}")
            await application.bot.send_message(chat_id=CHAT_ID, text=f"Sell signal generated for {SYMBOL}")
            await open_trade('sell', current_price, current_balance)

    except Exception as e:
        logging.error(f"Error in trade loop: {e}")
        await application.bot.send_message(chat_id=CHAT_ID, text=f"Error in trade loop: {e}")

# Telegram buyruqlari
async def start(update, context):
    await update.message.reply_text("Trading bot started! Use /balance to check balance.")
    schedule.every(5).minutes.do(lambda: application.run_async(trade))

async def balance(update, context):
    balance = get_balance()
    await update.message.reply_text(f"Current USDT balance: {balance}")

# Telegram handlerlari
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('balance', balance))

# Botni ishga tushirish
if __name__ == "__main__":
    logging.info("Bot started")
    application.bot.send_message(chat_id=CHAT_ID, text="Trading bot started!")
    application.run_polling(allowed_updates=["message"])
    while True:
        schedule.run_pending()
        time.sleep(1)
