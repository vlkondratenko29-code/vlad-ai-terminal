import os
import json
from aiohttp import web

STATE_FILE = "cloud_state.json"

DEFAULT_STATE = {
    "balance": 100.0,
    "net_pnl": 0.0,
    "roi": 0.0,
    "total": 0,
    "wins": 0,
    "losses": 0,
    "winrate": 0.0,
    "risk": 5.0,
    "auto_trade": True,
    "active": [],
    "history": [],
    "last_chart": "",
    "last_title": "Ожидание нового сигнала..."
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
    return web.json_response(load_state(), headers={"Cache-Control": "no-store"})


async def handle_sync(request):
    try:
        data = await request.json()
        if data.get("secret") != "vlad_sync_2026":
            return web.json_response({"ok": False}, status=403)
        old = load_state()
        new_st = data["state"]
        if not new_st.get("last_chart") and old.get("last_chart"):
            new_st["last_chart"] = old["last_chart"]
            new_st["last_title"] = old.get("last_title", "")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(new_st, f, ensure_ascii=False)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


app = web.Application(client_max_size=10 * 1024 * 1024)
app.router.add_get("/", handle_index)
app.router.add_get("/api/state", handle_state)
app.router.add_post("/api/sync", handle_sync)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
