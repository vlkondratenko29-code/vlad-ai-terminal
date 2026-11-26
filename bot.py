import os
import io
import json
import math
import time
import random
import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import ccxt.async_support as ccxt

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, WebAppInfo,
)

import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("VLAD")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

TRADES_FILE = "trades.json"
STATE_FILE = "positions_state.json"
TIKTOK_DIR = "tiktok_ready"
os.makedirs(TIKTOK_DIR, exist_ok=True)

# === АКТИВЫ: только 3, по итогам бэктеста ===
ASSETS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "GOLD": getattr(config, "GOLD_SYMBOL", "NCCOGOLD2USD/USDT:USDT"),
}

APP_URL = "https://vlad-ai-terminal.onrender.com?v=11"
VIP_TG_URL = "https://t.me/+Aws_KdmlcfFhNDk6"

# Глобальный клиент биржи (создаётся один раз)
EX = ccxt.bingx({
    "apiKey": getattr(config, "BINGX_API_KEY", ""),
    "secret": getattr(config, "BINGX_SECRET_KEY", ""),
    "options": {"defaultType": "swap", "adjustForTimeDifference": True, "recvWindow": 10000},
    "enableRateLimit": True,
})
MARKETS_LOADED = False

DRY_RUN = bool(getattr(config, "DRY_RUN", True))
AUTO_TRADE = bool(getattr(config, "AUTO_TRADE", False))


# ---------- утилиты ----------
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def has_valid_api():
    k = getattr(config, "BINGX_API_KEY", "")
    return bool(k and len(k) > 10)


def floor_amount(symbol, qty):
    """Округление ВНИЗ по точности инструмента."""
    m = EX.market(symbol)
    p = m.get("precision", {}).get("amount")
    if p is None:
        return qty
    if isinstance(p, float) or (isinstance(p, str) and "." in str(p)):
        step = float(p)
        return math.floor(qty / step) * step
    factor = 10 ** int(p)
    return math.floor(qty * factor) / factor


# ---------- обмен с биржей ----------
async def ensure_markets():
    global MARKETS_LOADED
    if not MARKETS_LOADED:
        await EX.load_markets()
        MARKETS_LOADED = True


async def get_balance():
    if not has_valid_api() or DRY_RUN:
        return float(getattr(config, "DEPOSIT", 100.0))
    try:
        bal = await EX.fetch_balance()
        usdt = bal.get("USDT", {})
        return round(float(usdt.get("total") or usdt.get("free") or 0), 2)
    except Exception as e:
        log.warning(f"balance err: {e}")
        return float(getattr(config, "DEPOSIT", 100.0))


async def get_open_positions():
    """Словарь открытых позиций: symbol -> position."""
    if DRY_RUN:
        return load_json(STATE_FILE, {})
    try:
        positions = await EX.fetch_positions()
        out = {}
        for p in positions:
            contracts = float(p.get("contracts") or 0)
            if contracts > 0:
                out[p["symbol"]] = p
        return out
    except Exception as e:
        log.warning(f"positions err: {e}")
        return {}


async def fetch_ohlcv(symbol, timeframe, limit):
    try:
        o = await EX.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not o:
            return None
        return pd.DataFrame(o, columns=["timestamp", "open", "high", "low", "close", "volume"])
    except Exception as e:
        log.warning(f"ohlcv {symbol} {timeframe}: {e}")
        return None


def compute_amount(symbol, setup, balance):
    """Сайзинг от стопа: риск = RISK_PCT от баланса."""
    entry = setup["entry"]
    sl_dist = abs(entry - setup["sl"])
    if sl_dist <= 0:
        return None
    risk_usd = balance * float(getattr(config, "RISK_PCT", 0.01))
    notional = risk_usd / (sl_dist / entry)
    cap = balance * float(getattr(config, "MAX_NOTIONAL_X", 2.0))
    notional = min(notional, cap)
    qty = floor_amount(symbol, notional / entry)
    m = EX.market(symbol)
    min_cost = (m.get("limits", {}).get("cost", {}) or {}).get("min") or 0
    if qty * entry < min_cost:
        qty = floor_amount(symbol, min_cost / entry * 1.01)
    min_amt = (m.get("limits", {}).get("amount", {}) or {}).get("min") or 0
    if qty < min_amt:
        qty = min_amt
    return float(qty), round(risk_usd, 2), round(qty * entry, 2)


# ---------- исполнение ----------
async def place_stop(symbol, direction, stop_price, amount):
    close_side = "sell" if direction == "LONG" else "buy"
    oid = f"sl_{int(time.time()*1000)}_{random.randint(100,999)}"
    return await EX.create_order(symbol, "STOP_MARKET", close_side, amount, None, {
        "stopPrice": stop_price, "positionSide": direction,
        "reduceOnly": True, "clientOrderId": oid,
    })


async def open_position(symbol, direction, setup, balance):
    """Вход + проверка стопа. Возвращает (ok, message)."""
    if DRY_RUN:
        log.info(f"[DRY] {direction} {symbol} entry={setup['entry']} sl={setup['sl']}")
        return True, "🧪 DRY_RUN: сигнал записан (ордер не отправлен)"

    calc = compute_amount(symbol, setup, balance)
    if not calc:
        return False, "не удалось рассчитать объём"
    amount, risk_usd, notional = calc

    side = "buy" if direction == "LONG" else "sell"
    try:
        await EX.set_margin_mode("isolated", symbol)
        await EX.set_leverage(int(getattr(config, "LEVERAGE", 5)), symbol)
    except Exception as e:
        log.error(f"margin/leverage fail {symbol}: {e}")
        return False, f"❌ не выставились margin/leverage: {e}"

    entry_oid = f"in_{int(time.time()*1000)}_{random.randint(100,999)}"
    try:
        # maker-вход (post-only лимитка)
        await EX.create_order(symbol, "limit", side, amount, setup["entry"], {
            "postOnly": True, "positionSide": direction, "clientOrderId": entry_oid,
        })
        # ждём исполнения до 12 сек; иначе отменяем и идём в market
        filled = False
        for _ in range(6):
            await asyncio.sleep(2)
            try:
                pos = (await get_open_positions()).get(symbol)
                if pos and float(pos.get("contracts") or 0) > 0:
                    filled = True
                    break
            except Exception:
                pass
        if not filled:
            try:
                await EX.cancel_all_orders(symbol)
            except Exception:
                pass
            await EX.create_order(symbol, "market", side, amount, None, {
                "positionSide": direction, "clientOrderId": entry_oid + "m",
            })
    except Exception as e:
        log.error(f"entry fail {symbol}: {e}")
        return False, f"❌ ошибка входа: {e}"

    # проверяем и ставим стоп
    await asyncio.sleep(1)
    try:
        open_orders = await EX.fetch_open_orders(symbol)
        has_sl = any(o.get("stopPrice") or "stop" in str(o.get("type", "")).lower() for o in open_orders)
        if not has_sl:
            await place_stop(symbol, direction, setup["sl"], amount)
    except Exception as e:
        log.error(f"stop fail {symbol}: {e}")

    # финальная проверка: если стопа так и нет — закрываем позицию
    await asyncio.sleep(1)
    try:
        oo = await EX.fetch_open_orders(symbol)
        has_sl = any(o.get("stopPrice") or "stop" in str(o.get("type", "")).lower() for o in oo)
        if not has_sl:
            log.error(f"NO STOP on {symbol}, closing position")
            close_side = "sell" if direction == "LONG" else "buy"
            await EX.create_order(symbol, "market", close_side, amount, None,
                                  {"positionSide": direction, "reduceOnly": True})
            return False, "⚠️ стоп не встал, позиция закрыта вручную"
    except Exception as e:
        log.error(f"stop verify fail: {e}")

    # тейки
    try:
        close_side = "sell" if direction == "LONG" else "buy"
        half = floor_amount(symbol, amount / 2)
        rest = float(amount) - float(half)
        if half > 0 and rest > 0:
            await EX.create_order(symbol, "TAKE_PROFIT_MARKET", close_side, half, None,
                                  {"stopPrice": setup["tp1"], "positionSide": direction,
                                   "reduceOnly": True, "clientOrderId": f"tp1_{entry_oid}"})
            await EX.create_order(symbol, "TAKE_PROFIT_MARKET", close_side, rest, None,
                                  {"stopPrice": setup["tp2"], "positionSide": direction,
                                   "reduceOnly": True, "clientOrderId": f"tp2_{entry_oid}"})
        else:
            await EX.create_order(symbol, "TAKE_PROFIT_MARKET", close_side, amount, None,
                                  {"stopPrice": setup["tp2"], "positionSide": direction,
                                   "reduceOnly": True, "clientOrderId": f"tp2_{entry_oid}"})
    except Exception as e:
        log.warning(f"tp fail {symbol}: {e}")

    return True, f"✅ ОРДЕР ОТКРЫТ (объём {amount}, риск ${risk_usd})"


# ---------- мониторинг позиций: безубыток после TP1 + запись реального PnL ----------
async def position_monitor():
    log.info("🛰 монитор позиций запущен")
    while True:
        try:
            positions = await get_open_positions()
            state = load_json(STATE_FILE, {})

            for symbol, pos in positions.items():
                contracts = float(pos.get("contracts") or 0)
                st = state.get(symbol, {})
                orig = float(st.get("orig_amount") or contracts)
                if not st:
                    state[symbol] = {
                        "orig_amount": contracts,
                        "entry": float(pos.get("entryPrice") or 0),
                        "dir": pos.get("side", "").upper() if pos.get("side") else "",
                        "tp1_done": False,
                    }
                    save_json(STATE_FILE, state)
                    continue

                # TP1 сработал (объём уменьшился) → стоп в безубыток
                if not st.get("tp1_done") and contracts < orig * 0.999 and contracts > 0:
                    entry_price = st.get("entry")
                    if entry_price and not DRY_RUN:
                        try:
                            await EX.cancel_all_orders(symbol)
                            await place_stop(symbol, st["dir"], entry_price, contracts)
                            st["tp1_done"] = True
                            state[symbol] = st
                            save_json(STATE_FILE, state)
                            log.info(f"♻️ {symbol}: стоп перенесён в безубыток после TP1")
                        except Exception as e:
                            log.warning(f"BE fail {symbol}: {e}")

            # закрытые позиции → записываем реальный PnL
            for symbol in list(state.keys()):
                if symbol not in positions:
                    st = state.pop(symbol)
                    pnl = await realized_pnl(symbol)
                    save_trade({
                        "date": datetime.now(timezone.utc).strftime("%d.%m %H:%M"),
                        "asset": sym_to_asset(symbol),
                        "dir": st.get("dir", ""),
                        "entry": st.get("entry", 0),
                        "pnl": pnl,
                    })
                    save_json(STATE_FILE, state)
                    await notify_close(sym_to_asset(symbol), pnl)
        except Exception as e:
            log.warning(f"monitor err: {e}")
        await asyncio.sleep(30)


def sym_to_asset(symbol):
    rev = {v: k for k, v in ASSETS.items()}
    return rev.get(symbol, symbol)


async def realized_pnl(symbol):
    """Фактический PnL с биржи. Если не удалось — вернём 0.0 и пометим в логе."""
    if DRY_RUN:
        return 0.0
    try:
        trades = await EX.fetch_my_trades(symbol, limit=30)
        total = 0.0
        found = False
        for t in trades:
            rp = float(t.get("info", {}).get("realizedPnl") or 0)
            if rp != 0:
                total += rp
                found = True
        if found:
            return round(total, 2)
    except Exception as e:
        log.warning(f"realizedPnl {symbol}: {e}")
    return 0.0


def save_trade(record):
    h = load_json(TRADES_FILE, [])
    h.append(record)
    save_json(TRADES_FILE, h)


async def notify_close(asset, pnl):
    if pnl is None:
        pnl = 0.0
    bal = await get_balance()
    icon = "🚀 ПРИБЫЛЬ" if pnl > 0 else "🛑 СТОП"
    try:
        await bot.send_message(
            chat_id=config.ADMIN_ID,
            text=f"🏁 <b>Сделка закрыта: {asset}</b>\n"
                 f"Итог: <b>{icon} {pnl:+.2f}$</b>\n"
                 f"💰 Баланс BingX: <b>${bal}</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.warning(f"notify err: {e}")


# ---------- сигнальная логика (та же, но с закрытой 1H свечой и прогретой EMA200) ----------
def analyze_setup(df1h, df15):
    # берём ЗАКРЫТУЮ 1H свечу (последняя ещё формируется)
    h = df1h.iloc[-2]
    ema20_1h = df1h["close"].ewm(span=20, adjust=False).mean().iloc[-2]
    ema50_1h = df1h["close"].ewm(span=50, adjust=False).mean().iloc[-2]
    ema200_1h = df1h["close"].ewm(span=200, adjust=False).mean().iloc[-2]
    price_1h = float(h["close"])
    if abs(ema20_1h - ema50_1h) / price_1h < 0.0018:
        return None

    df15 = df15.copy()
    df15["ema20"] = df15["close"].ewm(span=20, adjust=False).mean()
    tr = pd.concat([
        df15["high"] - df15["low"],
        (df15["high"] - df15["close"].shift()).abs(),
        (df15["low"] - df15["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df15["atr"] = tr.rolling(14).mean()
    d = df15["close"].diff()
    g = d.clip(lower=0).rolling(14).mean().iloc[-2]
    l = (-d.clip(upper=0)).rolling(14).mean().iloc[-2]
    rsi15 = 100 - (100 / (1 + (g / l))) if l != 0 else 50

    prev = df15.iloc[-3]
    curr = df15.iloc[-2]
    price = float(curr["close"])
    ema20 = float(curr["ema20"])
    atr = float(curr["atr"])
    if not atr or atr <= 0 or pd.isna(atr):
        return None
    sl_dist = atr * 2.6

    if price_1h > ema200_1h and ema20_1h > ema50_1h and rsi15 <= 68:
        touched = prev["low"] <= prev["ema20"] * 1.0015 or curr["low"] <= ema20 * 1.0015
        if touched and curr["close"] > curr["open"] and price > ema20:
            return mk_setup("LONG", price, sl_dist)
    if price_1h < ema200_1h and ema20_1h < ema50_1h and rsi15 >= 32:
        touched = prev["high"] >= prev["ema20"] * 0.9985 or curr["high"] >= ema20 * 0.9985
        if touched and curr["close"] < curr["open"] and price < ema20:
            return mk_setup("SHORT", price, sl_dist)
    return None


def mk_setup(direction, price, sl_dist):
    s = {
        "dir": direction,
        "entry": round(price, 4),
        "sl": round(price - sl_dist, 4) if direction == "LONG" else round(price + sl_dist, 4),
    }
    s["tp1"] = round(price + sl_dist * 1.0, 4) if direction == "LONG" else round(price - sl_dist * 1.0, 4)
    s["tp2"] = round(price + sl_dist * 1.8, 4) if direction == "LONG" else round(price - sl_dist * 1.8, 4)
    return s


# ---------- график ----------
def generate_chart(df15, asset, direction, setup):
    try:
        view = df15.tail(45).reset_index(drop=True)
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=130)
        fig.patch.set_facecolor("#0b0e14")
        ax.set_facecolor("#131722")
        for i, row in view.iterrows():
            color = "#00e676" if row["close"] >= row["open"] else "#ff1744"
            ax.plot([i, i], [row["low"], row["high"]], color=color, linewidth=1.2)
            bottom = min(row["open"], row["close"])
            height = max(abs(row["close"] - row["open"]), (row["high"] - row["low"]) * 0.05)
            ax.bar(i, height, bottom=bottom, width=0.6, color=color)
        ema = view["close"].ewm(span=20, adjust=False).mean()
        ax.plot(range(len(view)), ema, color="#2979ff", linewidth=1.3)
        for lvl, c, lbl in [(setup["entry"], "#ffb300", "Entry"), (setup["sl"], "#ff1744", "SL"),
                            (setup["tp1"], "#00e676", "TP1"), (setup["tp2"], "#00e676", "TP2")]:
            ax.axhline(lvl, color=c, linestyle="--", linewidth=1.0)
        ax.set_title(f"VLAD AI TERMINAL • {asset} ({direction}) 15m", color="#e0e6ed", fontsize=11)
        ax.tick_params(colors="#787b86", labelsize=8)
        ax.grid(True, color="#1e222d", linewidth=0.6)
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", facecolor=fig.get_facecolor())
        img = buf.getvalue()
        plt.close(fig)
        return img
    except Exception as e:
        log.warning(f"chart err: {e}")
        return None


# ---------- дневной лимит убытка ----------
DAY_STATE = {"date": None, "start_balance": None}

async def daily_limit_hit(balance):
    today = datetime.now(timezone.utc).date()
    if DAY_STATE["date"] != today:
        DAY_STATE["date"] = today
        DAY_STATE["start_balance"] = balance
        return False
    start = DAY_STATE["start_balance"] or balance
    limit = start * float(getattr(config, "DAILY_LOSS_LIMIT_PCT", 0.02))
    return (start - balance) >= limit


# ---------- сканер ----------
LAST_SIGNAL_CANDLE = {}

async def market_scanner():
    log.info("🚀 сканер запущен")
    await ensure_markets()
    while True:
        try:
            balance = await get_balance()
            if await daily_limit_hit(balance):
                log.info("⏸ дневной лимит убытка достигнут, пауза до завтра")
                await asyncio.sleep(60)
                continue

            positions = await get_open_positions()
            open_count = len(positions)

            for asset, symbol in ASSETS.items():
                if symbol in positions:
                    continue
                if open_count >= int(getattr(config, "MAX_CONCURRENT", 2)):
                    continue

                df1h = await fetch_ohlcv(symbol, "1h", limit=300)   # прогрев EMA200
                if df1h is None or len(df1h) < 220:
                    continue
                df15 = await fetch_ohlcv(symbol, "15m", limit=90)
                if df15 is None or len(df15) < 60:
                    continue

                closed = df15.iloc[-2]
                candle_ts = str(closed["timestamp"])
                if LAST_SIGNAL_CANDLE.get(asset) == candle_ts:
                    continue

                setup = analyze_setup(df1h, df15)
                if setup:
                    LAST_SIGNAL_CANDLE[asset] = candle_ts
                    direction = setup["dir"]

                    if AUTO_TRADE:
                        ok, msg = await open_position(symbol, direction, setup, balance)
                    else:
                        ok, msg = False, "AUTO_TRADE выключен"

                    # сохраняем состояние позиции (для безубытка и журнала)
                    st = load_json(STATE_FILE, {})
                    st[symbol] = {
                        "orig_amount": 0, "entry": setup["entry"],
                        "dir": direction, "tp1_done": False,
                    }
                    save_json(STATE_FILE, st)

                    img = generate_chart(df15, asset, direction, setup)
                    caption = (
                        f"⚡ <b>СИГНАЛ: {asset} ({direction})</b>\n"
                        f"🎯 Вход: <code>{setup['entry']}</code>\n"
                        f"🛑 Стоп: <code>{setup['sl']}</code>\n"
                        f"✅ TP1: <code>{setup['tp1']}</code>\n"
                        f"🚀 TP2: <code>{setup['tp2']}</code>\n\n"
                        f"🤖 {msg}\n💰 Баланс: <b>${balance}</b> (риск {config.RISK_PCT*100:.1f}%)"
                    )
                    if img:
                        await bot.send_photo(chat_id=config.ADMIN_ID,
                                             photo=BufferedInputFile(img, filename=f"{asset}.png"),
                                             caption=caption, parse_mode=ParseMode.HTML)
                        vip_id = getattr(config, "VIP_CHANNEL_ID", 0)
                        if vip_id:
                            try:
                                await bot.send_photo(chat_id=vip_id,
                                                     photo=BufferedInputFile(img, filename=f"{asset}_vip.png"),
                                                     caption=caption, parse_mode=ParseMode.HTML)
                            except Exception as e:
                                log.warning(f"vip err: {e}")
                    else:
                        await bot.send_message(chat_id=config.ADMIN_ID, text=caption, parse_mode=ParseMode.HTML)

                    if ok:
                        open_count += 1
                await asyncio.sleep(1.5)
        except Exception as e:
            log.error(f"scanner err: {e}")
        await asyncio.sleep(45)


# ---------- команды ----------
@dp.message(Command("start", "menu"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ ОТКРЫТЬ ТЕРМИНАЛ", web_app=WebAppInfo(url=APP_URL))],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")],
        [InlineKeyboardButton(text="💎 Канал с сигналами", url=VIP_TG_URL)],
    ])
    mode = "DRY_RUN" if DRY_RUN else ("LIVE" if AUTO_TRADE else "ПАУЗА")
    await message.answer(
        f"👋 <b>VLAD AI TERMINAL</b>\n\n"
        f"Режим: <b>{mode}</b>\n"
        f"Активы: BTC, ETH, GOLD\n"
        f"Риск: {config.RISK_PCT*100:.1f}% | Плечо {config.LEVERAGE}x\n"
        f"Сценарий: maker-вход + безубыток после TP1",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    bal = await get_balance()
    positions = await get_open_positions()
    h = load_json(TRADES_FILE, [])
    total = len(h)
    wins = sum(1 for t in h if float(t.get("pnl", 0)) > 0)
    wr = round((wins / total) * 100, 1) if total else 0.0
    net = round(sum(float(t.get("pnl", 0)) for t in h), 2)
    open_syms = ", ".join(sym_to_asset(s) for s in positions.keys()) or "нет"
    await message.answer(
        f"📊 <b>LIVE-СТАТИСТИКА</b>\n\n"
        f"💰 Баланс: <b>${bal}</b>\n"
        f"📈 Открыто: <b>{open_syms}</b>\n"
        f"📋 Закрыто: <b>{total}</b> ({wins}W/{total-wins}L • {wr}%)\n"
        f"💵 PnL по журналу: <b>{net:+.2f}$</b>",
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "show_stats")
async def cb_stats(call: types.CallbackQuery):
    await call.answer()
    await cmd_stats(call.message)


async def main():
    asyncio.create_task(market_scanner())
    asyncio.create_task(position_monitor())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())