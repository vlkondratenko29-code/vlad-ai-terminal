import os
import json
import aiohttp
from aiohttp import web

STATE_FILE = "cloud_state.json"
SYNC_SECRET = os.getenv("SYNC_SECRET", "")

BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "GOLD": "PAXGUSDT",
    "XRP": "XRPUSDT",
    "SUI": "SUIUSDT",
}

NEUTRAL_STATE = {
    "balance": 0.0, "net_pnl": 0.0, "roi": 0.0,
    "total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
    "risk": 0.0, "auto_trade": False,
    "active": [], "history": [],
    "last_chart": "", "last_title": "",
}


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return NEUTRAL_STATE


async def handle_index(request):
    return web.FileResponse("app.html")


async def handle_state(request):
    return web.json_response(load_state())


async def handle_sync(request):
    try:
        data = await request.json()
        if not SYNC_SECRET or data.get("secret") != SYNC_SECRET:
            return web.json_response({"ok": False}, status=403)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data.get("state", {}), f, ensure_ascii=False)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_candles(request):
    asset = request.query.get("asset", "BTC")
    symbol = BINANCE_SYMBOLS.get(asset, "BTCUSDT")
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=15m&limit=65"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=8) as resp:
                raw = await resp.json()
        candles = [
            {
                "time": int(c[0] // 1000),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
            }
            for c in raw
        ]
        return web.json_response({"candles": candles})
    except Exception as e:
        return web.json_response({"candles": [], "error": str(e)})


app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_get("/api/state", handle_state)
app.router.add_post("/api/sync", handle_sync)
app.router.add_get("/api/candles", handle_candles)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
