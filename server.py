import os
from aiohttp import web
import ccxt.async_support as ccxt

ASSETS = {
    "GOLD": "NCCOGOLD2USD/USDT:USDT",
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
    "XRP": "XRP/USDT:USDT",
    "SUI": "SUI/USDT:USDT",
}


async def handle_index(request):
    return web.FileResponse("app.html")


async def handle_candles(request):
    asset = request.query.get("asset", "GOLD")
    symbol = ASSETS.get(asset, ASSETS["GOLD"])
    ex = ccxt.bingx({"options": {"defaultType": "swap"}})
    try:
        ohlcv = await ex.fetch_ohlcv(symbol, "15m", limit=65)
        candles = [
            {
                "time": int(c // 1000),
                "open": float(c),
                "high": float(c),
                "low": float(c),
                "close": float(c),
            }
            for c in ohlcv
        ]
        return web.json_response({"candles": candles})
    except Exception as e:
        return web.json_response({"candles": [], "error": str(e)})
    finally:
        await ex.close()


app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_get("/api/candles", handle_candles)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
