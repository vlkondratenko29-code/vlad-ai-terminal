import os
import json
import aiohttp
from aiohttp import web

STATE_FILE = "cloud_state.json"

BINANCE_SYMBOLS = {
    "GOLD": "PAXGUSDT",
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "SUI": "SUIUSDT",
}

DEFAULT_STATE = {
    "balance": 95.0,
    "net_pnl": -5.0,
    "roi": -5.0,
    "total": 1,
    "wins": 0,
    "losses": 1,
    "winrate": 0.0,
    "risk": 5.0,
    "auto_trade": True,
    "active": [],
    "history": [
        {"date": "23.11 23:10", "asset": "ETH", "dir": "LONG", "pnl": -5.0}
    ],
}


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_STATE


async def handle_index(request):
    return web.FileResponse("app.html", headers={"Cache-Control": "no-store"})


async def handle_state(request):
    return web.json_response(load_state())


async def handle_sync(request):
    try:
        data = await request.json()
        if data.get("secret") != "vlad_sync_2026":
            return web.json_response({"ok": False}, status=403)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data["state"], f, ensure_ascii=False)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_candles(request):
    asset = request.query.get("asset", "GOLD")
    symbol = BINANCE_SYMBOLS.get(asset, "PAXGUSDT")
    url = f"https://binance.vision{symbol}&interval=15m&limit=55"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as resp:
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
