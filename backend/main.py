# ═══════════════════════════════════════════════════════════════
# Файл: main.py (главный файл сервера)
# Назначение: FastAPI-приложение — все эндпоинты API.
# Что происходит: Определяет 4 эндпоинта:
#   POST /walk/ping     — принять GPS-координаты, начислить монеты
#   POST /admin/console — выполнить админ-команду
#   POST /shop/buy      — купить бафф
#   GET  /{path}        — отдать фронтенд (index.html и статика)
# ═══════════════════════════════════════════════════════════════

from pathlib import Path       # Работа с путями файлов (кросс-платформенно)
import time                    # Модуль времени (для timestamp)

from fastapi import FastAPI, HTTPException       # FastAPI — веб-фреймворк,
                                                 # HTTPException — ошибка с кодом
from fastapi.responses import FileResponse       # Отдаёт файл как HTTP-ответ
from geopy.distance import geodesic              # Расчёт расстояния между координатами

from backend.auth import extract_user_id, validate_init_data    # Проверка initData
from backend.config import ADMIN_IDS, METERS_PER_COIN, MIN_PING_INTERVAL_S, SPEED_LIMIT_KMH
from backend.schemas import AdminConsoleRequest, ShopBuyRequest, WalkPingRequest
from backend.storage import BUFFS, storage


# Создаём FastAPI-приложение. title — название, видно в Swagger (/docs)
app = FastAPI(title="Walk to Earn")


# ═══════════════════════════════════════════════════════════════
# ЗАПУСК И ОСТАНОВКА: подключение к БД
# ═══════════════════════════════════════════════════════════════

@app.on_event("startup")          # FastAPI вызывает эту функцию при старте сервера
async def on_startup():
    # Создаём пул подключений к PostgreSQL (открывает 1–5 соединений).
    # Без этого шага storage не сможет работать с БД.
    await storage.connect()


@app.on_event("shutdown")         # FastAPI вызывает эту функцию при остановке сервера
async def on_shutdown():
    # Закрываем пул подключений — ждём завершения текущих запросов
    # и закрываем все TCP-соединения с PostgreSQL.
    await storage.disconnect()


# ═══════════════════════════════════════════════════════════════
# ЭНДПОИНТ 1: POST /walk/ping — отправить GPS и получить монеты
# ═══════════════════════════════════════════════════════════════

@app.post("/walk/ping")
async def walk_ping(body: WalkPingRequest):
    # async def — все эндпоинты теперь асинхронные, потому что storage
    # работает с PostgreSQL через asyncpg (иначе сервер зависал бы на каждом запросе к БД).
    #
    # body — это JSON, который FastAPI автоматически превращает
    # в объект WalkPingRequest (благодаря Pydantic)

    # Проверяем initData — если хэш не совпал, validate вернёт None
    parsed = validate_init_data(body.init_data)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Invalid init_data")
        # 401 Unauthorized — данные не прошли проверку

    # Извлекаем Telegram ID пользователя из initData
    user_id = extract_user_id(parsed)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid user data in init_data")

    # Берём профиль пользователя (или создаём нового)
    user = await storage.get_or_create(user_id)

    # Если пользователь забанен — отклоняем запрос
    if user.is_banned:
        raise HTTPException(status_code=403, detail="User is banned")
        # 403 Forbidden — доступ запрещён

    # Проверяем активные баффы и меняем параметры игры
    effective_speed_limit = SPEED_LIMIT_KMH  # Обычный лимит скорости (15 км/ч)
    coin_multiplier = 1                      # Обычный множитель монет (x1)

    # Если активен бафф speed_boost — поднимаем лимит до 25 км/ч
    if "speed_boost" in user.active_buffs:
        effective_speed_limit = 25.0

    # Если активен бафф x2_coins — удваиваем монеты
    if "x2_coins" in user.active_buffs:
        coin_multiplier = 2

    now = time.time()  # Текущее время (в секундах с 1970 года)

    # Если у пользователя уже был пинг — проверяем интервал
    if user.last_ping_time is not None:
        elapsed = now - user.last_ping_time  # Сколько прошло секунд с прошлого раза

        # Если прошло меньше MIN_PING_INTERVAL_S (10 секунд) — ошибка
        if elapsed < MIN_PING_INTERVAL_S:
            raise HTTPException(
                status_code=429,  # 429 Too Many Requests
                detail=f"Too frequent. Wait {MIN_PING_INTERVAL_S - elapsed:.0f}s",
            )

    # Если у пользователя есть предыдущие координаты — считаем расстояние и скорость
    if user.last_lat is not None and user.last_lon is not None:
        prev = (user.last_lat, user.last_lon)  # Предыдущая точка
        curr = (body.lat, body.lon)            # Текущая точка

        # geodesic считает расстояние по поверхности Земли (в метрах)
        distance_m = geodesic(prev, curr).meters

        # Время в часах между пингами (для расчёта скорости)
        elapsed_h = (now - user.last_ping_time) / 3600

        # Скорость: (метры / 1000 = километры) / часы = км/ч
        speed_kmh = (distance_m / 1000) / elapsed_h if elapsed_h > 0 else 0.0

    else:
        # Если это первый пинг — нет предыдущей точки, расстояние = 0
        distance_m = 0.0
        speed_kmh = 0.0

    # Собираем список активных баффов для ответа (фронтенду)
    active_buffs = list(user.active_buffs.keys()) if user.active_buffs else []

    # Если скорость превышает лимит — обновляем позицию, но монеты НЕ начисляем
    if speed_kmh > effective_speed_limit:
        await storage.update_position(user_id, body.lat, body.lon, distance_m)
        return {
            "status": "speed_limit_exceeded",  # Статус: превышение скорости
            "coins_earned": 0,                 # Монет не заработано
            "total_coins": user.balance,       # Текущий баланс
            "distance_m": round(distance_m, 2), # Пройдено метров
            "speed_kmh": round(speed_kmh, 2),  # Скорость в км/ч
            "active_buffs": active_buffs,       # Активные баффы
        }

    # Если скорость в норме — начисляем монеты
    # Делим метры на METERS_PER_COIN (10), округляем вниз (int),
    # умножаем на множитель от баффов
    coins_earned = int(distance_m / METERS_PER_COIN) * coin_multiplier

    if coins_earned > 0:
        user.balance += coins_earned  # Добавляем монеты на баланс

    # Сохраняем новую позицию пользователя
    await storage.update_position(user_id, body.lat, body.lon, distance_m)

    # Возвращаем результат
    return {
        "status": "ok",                    # Успешно
        "coins_earned": coins_earned,      # Сколько монет заработано сейчас
        "total_coins": user.balance,       # Общий баланс
        "distance_m": round(distance_m, 2), # Дистанция в метрах
        "speed_kmh": round(speed_kmh, 2),  # Скорость в км/ч
        "active_buffs": active_buffs,       # Активные баффы
    }


# ═══════════════════════════════════════════════════════════════
# ЭНДПОИНТ 2: POST /admin/console — админ-команды
# ═══════════════════════════════════════════════════════════════

@app.post("/admin/console")
async def admin_console(body: AdminConsoleRequest):
    # async def — потому что storage работает с PostgreSQL (асинхронный драйвер asyncpg).
    # Проверяем initData
    parsed = validate_init_data(body.init_data)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Invalid init_data")

    user_id = extract_user_id(parsed)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid user data in init_data")

    # Проверяем, является ли пользователь администратором
    if user_id not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Not an admin")

    # Разбиваем текст команды по пробелам: "/addmoney 123 500" → ["/addmoney", "123", "500"]
    parts = body.command.strip().split()

    if not parts:  # Если команда пустая
        raise HTTPException(status_code=400, detail="Empty command")

    cmd = parts[0]  # Первое слово — это имя команды

    # ── Команда /addmoney <user_id> <amount> ──
    if cmd == "/addmoney":
        if len(parts) != 3:  # Должно быть ровно 3 части: команда, ID, сумма
            raise HTTPException(status_code=400, detail="Usage: /addmoney <user_id> <amount>")

        target_id = int(parts[1])  # ID пользователя, которому начисляем
        amount = int(parts[2])     # Сумма монет

        # Берём или создаём профиль целевого пользователя
        user = await storage.get_or_create(target_id)
        user.balance += amount  # Начисляем монеты в обход проверок
        # Сохраняем изменения в БД (раньше storage был in-memory,
        # теперь любое изменение нужно явно сохранять через await storage.save())
        await storage.save(user)

        return {
            "status": "ok",
            "message": f"Added {amount} coins to user {target_id}",
            "new_balance": user.balance,
        }

    # ── Команда /ban <user_id> ──
    if cmd == "/ban":
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail="Usage: /ban <user_id>")

        target_id = int(parts[1])
        user = await storage.ban(target_id)  # Баним пользователя

        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        return {"status": "ok", "message": f"User {target_id} banned"}

    # ── Команда /clear_cache ──
    if cmd == "/clear_cache":
        return {"status": "ok", "message": "Cache cleared"}

    # Если команда не подошла ни под один if — она неизвестна
    raise HTTPException(status_code=400, detail=f"Unknown command: {cmd}")


# ═══════════════════════════════════════════════════════════════
# ЭНДПОИНТ 3: POST /shop/buy — покупка баффа
# ═══════════════════════════════════════════════════════════════

@app.post("/shop/buy")
async def shop_buy(body: ShopBuyRequest):
    # async def — асинхронный эндпоинт для работы с PostgreSQL.
    # Проверяем initData
    parsed = validate_init_data(body.init_data)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Invalid init_data")

    user_id = extract_user_id(parsed)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid user data in init_data")

    # Проверяем, существует ли такой предмет в магазине
    if body.item_id not in BUFFS:
        raise HTTPException(status_code=400, detail="Unknown item")

    # Покупаем бафф. buy_buff сам спишет монеты и активирует.
    # Если вернул None — не хватило денег.
    user = await storage.buy_buff(user_id, body.item_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Not enough coins")

    return {
        "status": "ok",
        "balance": user.balance,                     # Новый баланс
        "active_buffs": list(user.active_buffs.keys()),  # Список активных баффов
    }


# ═══════════════════════════════════════════════════════════════
# СТАТИКА: раздача фронтенда
# ═══════════════════════════════════════════════════════════════

# Определяем пути к папке frontend
# __file__ — путь к этому файлу (main.py)
# .resolve() — превращает в абсолютный путь
# .parent — папка backend/
# .parent — корневая папка проекта walk-to-earn/
# / "frontend" — папка frontend/
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Путь к index.html
INDEX_HTML = FRONTEND_DIR / "index.html"

# Расширения файлов, которые можно отдавать как статику
STATIC_EXTENSIONS = frozenset({
    ".html", ".js", ".css", ".json", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp"
})


# ═══════════════════════════════════════════════════════════════
# ЭНДПОИНТ 4: GET /{path} — отдать фронтенд
# ═══════════════════════════════════════════════════════════════

@app.get("/{path:path}")
def serve_frontend(path: str):
    # Любой GET-запрос ловится этим эндпоинтом.
    # path — всё, что после / (например, "styles/main.css" или "" для корня)

    target = FRONTEND_DIR / path  # Собираем полный путь к файлу

    # Если файл существует и его расширение в списке разрешённых
    if target.is_file() and target.suffix in STATIC_EXTENSIONS:
        return FileResponse(str(target))  # Отдаём этот файл

    # Иначе отдаём index.html (SPA-подход — все пути ведут на главную)
    return FileResponse(str(INDEX_HTML))
