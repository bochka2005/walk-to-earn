from pathlib import Path
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from geopy.distance import geodesic

from auth import extract_user_id, validate_init_data
from config import ADMIN_IDS, METERS_PER_COIN, MIN_PING_INTERVAL_S, SPEED_LIMIT_KMH
from schemas import AdminConsoleRequest, UserRequest, WalkPingRequest
from storage import storage

app = FastAPI(title="Walk to Earn")


@app.on_event("startup")
async def on_startup():
    await storage.connect()


@app.on_event("shutdown")
async def on_shutdown():
    await storage.disconnect()


@app.post("/walk/ping")
async def walk_ping(body: WalkPingRequest):
    parsed = validate_init_data(body.init_data)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Invalid init_data")

    user_id = extract_user_id(parsed)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid user data")

    user = await storage.get_or_create(user_id)

    if user.is_banned:
        raise HTTPException(status_code=403, detail="User is banned")

    now = time.time()

    if user.last_ping_time is not None:
        elapsed = now - user.last_ping_time
        if elapsed < MIN_PING_INTERVAL_S:
            raise HTTPException(
                status_code=429,
                detail=f"Too frequent. Wait {MIN_PING_INTERVAL_S - elapsed:.0f}s",
            )

    if user.last_lat is not None and user.last_lon is not None:
        prev = (user.last_lat, user.last_lon)
        curr = (body.lat, body.lon)
        distance_m = geodesic(prev, curr).meters
        elapsed_h = (now - user.last_ping_time) / 3600
        speed_kmh = (distance_m / 1000) / elapsed_h if elapsed_h > 0 else 0.0
    else:
        distance_m = 0.0
        speed_kmh = 0.0

    if speed_kmh > SPEED_LIMIT_KMH:
        user = await storage.update_position(user_id, body.lat, body.lon, distance_m, 0)
        return {
            "status": "speed_limit_exceeded",
            "coins_earned": 0,
            "total_coins": user.balance,
            "total_distance_m": round(user.total_distance_m, 2),
            "distance_m": round(distance_m, 2),
            "speed_kmh": round(speed_kmh, 2),
        }

    coins_earned = int(distance_m / METERS_PER_COIN)
    user = await storage.update_position(user_id, body.lat, body.lon, distance_m, coins_earned)

    return {
        "status": "ok",
        "coins_earned": coins_earned,
        "total_coins": user.balance,
        "total_distance_m": round(user.total_distance_m, 2),
        "distance_m": round(distance_m, 2),
        "speed_kmh": round(speed_kmh, 2),
    }


@app.post("/user")
async def get_user(body: UserRequest):
    parsed = validate_init_data(body.init_data)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Invalid init_data")
    user_id = extract_user_id(parsed)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid user data")
    user = await storage.get_or_create(user_id)
    return {
        "balance": user.balance,
        "total_distance_m": round(user.total_distance_m, 2),
    }


@app.post("/admin/console")
async def admin_console(body: AdminConsoleRequest):
    parsed = validate_init_data(body.init_data)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Invalid init_data")

    user_id = extract_user_id(parsed)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid user data")

    if user_id not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Not an admin")

    parts = body.command.strip().split()
    if not parts:
        raise HTTPException(status_code=400, detail="Empty command")

    cmd = parts[0]

    if cmd == "/addmoney":
        if len(parts) != 3:
            raise HTTPException(status_code=400, detail="Usage: /addmoney <user_id> <amount>")
        target_id = int(parts[1])
        amount = int(parts[2])
        user = await storage.get_or_create(target_id)
        user.balance += amount
        await storage.save(user)
        return {"status": "ok", "message": f"Added {amount} coins to user {target_id}", "new_balance": user.balance}

    if cmd == "/ban":
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail="Usage: /ban <user_id>")
        target_id = int(parts[1])
        user = await storage.ban(target_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return {"status": "ok", "message": f"User {target_id} banned"}

    raise HTTPException(status_code=400, detail=f"Unknown command: {cmd}")


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
INDEX_HTML = FRONTEND_DIR / "index.html"
STATIC_EXTENSIONS = frozenset({
    ".html", ".js", ".css", ".json", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp"
})


@app.get("/{path:path}")
def serve_frontend(path: str):
    target = FRONTEND_DIR / path
    if target.is_file() and target.suffix in STATIC_EXTENSIONS:
        return FileResponse(str(target))
    return FileResponse(str(INDEX_HTML))
