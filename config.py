import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _s(name, default=""):
    return os.getenv(name, default).strip()


def _n(name, default):
    try:
        return float(_s(name, str(default)))
    except ValueError:
        return float(default)


def _b(name, default=False):
    return _s(name, str(default)).lower() in ("1", "true", "yes", "on")


# --- Секреты: только из окружения, в коде их нет ---
BOT_TOKEN        = _s("BOT_TOKEN")
BINGX_API_KEY    = _s("BINGX_API_KEY")
BINGX_SECRET_KEY = _s("BINGX_SECRET_KEY")
SYNC_SECRET      = _s("SYNC_SECRET")

ADMIN_ID       = int(_n("ADMIN_ID", 0))
VIP_CHANNEL_ID = int(_n("VIP_CHANNEL_ID", 0))

# --- Режим ---
AUTO_TRADE = _b("AUTO_TRADE", False)   # по умолчанию ВЫКЛ
USE_DEMO   = _b("USE_DEMO", True)      # заработает после Шага 5

# --- Деньги и риск ---
DEPOSIT  = _n("DEPOSIT", 100.0)
RISK_PCT = _n("RISK_PCT", 0.0075)      # 0.75% вместо 3.5%
RISK_USD = round(DEPOSIT * RISK_PCT, 2)

LEVERAGE             = int(_n("LEVERAGE", 5))
MAX_NOTIONAL_X       = _n("MAX_NOTIONAL_X", 2.0)
MAX_OPEN_RISK_PCT    = _n("MAX_OPEN_RISK_PCT", 0.015)
DAILY_LOSS_LIMIT_PCT = _n("DAILY_LOSS_LIMIT_PCT", 0.02)
MAX_DD_STOP_PCT      = _n("MAX_DD_STOP_PCT", 0.08)

GOLD_SYMBOL = _s("GOLD_SYMBOL", "XAUT/USDT:USDT")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения")