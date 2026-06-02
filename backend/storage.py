# ═══════════════════════════════════════════════════════════════
# Файл: storage.py
# Назначение: Хранилище данных в PostgreSQL через asyncpg.
# Что происходит: Все данные о пользователях (баланс, координаты,
# активные баффы) хранятся в таблице users в PostgreSQL.
# ═══════════════════════════════════════════════════════════════

import json       # Модуль для работы с JSON (превращаем active_buffs в строку и обратно)
import time       # Модуль времени (для timestamp — время истечения баффов)
from dataclasses import dataclass, field  # dataclass — сокращённая запись классов,
                                          # которые в основном хранят данные

import asyncpg    # Асинхронный драйвер PostgreSQL (подключается к БД без блокировки сервера)

from backend.config import DATABASE_URL   # Строка подключения к БД из конфига


# Словарь со всеми доступными баффами (предметами в магазине).
# Ключ — ID баффа (строка), значение — его свойства.
BUFFS: dict[str, dict] = {
    # x2 Coins — удваивает монеты на 30 минут (1800 секунд), стоит 500 монет
    "x2_coins": {"name": "x2 Coins", "price": 500, "duration": 1800},
    # Speed Boost — поднимает лимит скорости до 25 км/ч на 6 минут (360 секунд)
    "speed_boost": {"name": "Speed Boost", "price": 300, "duration": 360},
    # Auto-walk — пассивный доход (пока заглушка), на час (3600 секунд)
    "auto_walk": {"name": "Auto-walk", "price": 1000, "duration": 3600},
}


@dataclass
class UserProfile:
    # Профиль одного пользователя (только для чтения, БД — источник истины)
    id: int                     # Telegram ID пользователя
    balance: int = 0            # Баланс монет (по умолчанию 0)
    is_banned: bool = False     # Забанен ли (по умолчанию нет)
    last_lat: float | None = None   # Последняя широта (ещё не ходил — None)
    last_lon: float | None = None   # Последняя долгота (ещё не ходил — None)
    last_ping_time: float | None = None  # Время последнего пинга (timestamp или None)
    total_distance_m: float = 0.0       # Всего пройдено метров
    active_buffs: dict[str, float] = field(default_factory=dict)


async def row_to_profile(row: asyncpg.Record) -> UserProfile:
    # Превратить строку из БД (asyncpg.Record) в объект UserProfile.
    # Нужно, потому что asyncpg возвращает строки как Record, а нам удобнее
    # работать с нормальным Python-классом с именованными полями.

    # Берём active_buffs из БД. В PostgreSQL это JSONB, но asyncpg может
    # вернуть его как строку или как словарь — проверяем оба случая.
    active_buffs = row["active_buffs"]
    if active_buffs is not None and isinstance(active_buffs, str):
        active_buffs = json.loads(active_buffs)  # Из строки JSON → Python-словарь
    if active_buffs is None:
        active_buffs = {}  # Вместо None ставим пустой словарь

    # Создаём UserProfile, заполняя поля из строки БД
    return UserProfile(
        id=row["id"],                       # Telegram ID
        balance=row["balance"],             # Баланс монет
        is_banned=row["is_banned"],         # Забанен ли
        last_lat=row["last_lat"],           # Последняя широта
        last_lon=row["last_lon"],           # Последняя долгота
        last_ping_time=row["last_ping_time"],  # Время последнего пинга
        total_distance_m=row["total_distance_m"],  # Всего пройдено метров
        active_buffs=active_buffs,          # Активные баффы (словарь)
    )


class PostgresStorage:
    # Хранилище в PostgreSQL — вместо старого InMemoryStorage.
    # Все операции с данными — асинхронные (await), чтобы сервер
    # не зависал, пока БД обрабатывает запрос.

    def __init__(self) -> None:
        # Конструктор: создаём пустой pool (откроем при старте сервера)
        self._pool: asyncpg.Pool | None = None  # Пул подключений к PostgreSQL

    async def connect(self) -> None:
        # Создать пул подключений к PostgreSQL (вызывается при старте сервера).
        # Пул хранит 1-5 соединений и переиспользует их для всех запросов.
        self._pool = await asyncpg.create_pool(
            DATABASE_URL,  # Строка подключения (из config.py)
            min_size=1,    # Минимум 1 соединение всегда открыто
            max_size=5,    # Максимум 5 одновременных соединений
        )

        # Создать таблицу users, если её ещё нет (автомиграция при первом запуске).
        # IF NOT EXISTS — безопасно: если таблица уже есть, команда ничего не сломает.
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id              BIGINT PRIMARY KEY,      -- Telegram ID (число, ключ)
                    balance         INTEGER NOT NULL DEFAULT 0,  -- Баланс монет
                    is_banned       BOOLEAN NOT NULL DEFAULT FALSE,  -- Забанен ли
                    last_lat        DOUBLE PRECISION,        -- Последняя широта
                    last_lon        DOUBLE PRECISION,        -- Последняя долгота
                    last_ping_time  DOUBLE PRECISION,        -- Время последнего пинга
                    total_distance_m DOUBLE PRECISION NOT NULL DEFAULT 0.0,  -- Всего метров
                    active_buffs    JSONB NOT NULL DEFAULT '{}'  -- Баффы в JSON-формате
                )
            """)

    async def disconnect(self) -> None:
        # Закрыть пул подключений (вызывается при остановке сервера).
        # Ждём, пока все текущие запросы завершатся, потом закрываем соединения.
        if self._pool is not None:
            await self._pool.close()

    async def get_or_create(self, user_id: int) -> UserProfile:
        # Найти пользователя по ID. Если нет — создать нового в БД.
        # Используем INSERT ... ON CONFLICT DO NOTHING — это стандартный
        # приём "upsert" в PostgreSQL: если запись с таким id уже есть,
        # INSERT ничего не делает, и мы просто читаем существующую.
        #
        # RETURNING * — после INSERT сразу вернуть все поля новой строки.
        async with self._pool.acquire() as conn:            # Берём соединение из пула
            row = await conn.fetchrow("""                    # Выполняем SQL, ждём результат
                INSERT INTO users (id)                       # Пытаемся вставить
                VALUES ($1)                                  # $1 = user_id (безопасная подстановка)
                ON CONFLICT (id) DO NOTHING                  # Если уже есть — ничего не делаем
                RETURNING *                                  # Вернуть все поля (или ничего, если конфликт)
            """, user_id)
            if row is None:                                  # Конфликт: пользователь уже был
                row = await conn.fetchrow(                   #   Просто читаем его из БД
                    "SELECT * FROM users WHERE id = $1", user_id
                )

        profile = await row_to_profile(row)   # Record → UserProfile
        self._clean_expired_buffs(profile)    # Удаляем истёкшие баффы (в памяти)
        return profile

    async def get(self, user_id: int) -> UserProfile | None:
        # Найти пользователя. Если нет в БД — вернуть None.
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if row is None:
            return None            # Нет такого пользователя
        profile = await row_to_profile(row)
        self._clean_expired_buffs(profile)  # Чистим истёкшие баффы
        return profile

    def _clean_expired_buffs(self, user: UserProfile) -> None:
        # Удалить у пользователя баффы, у которых истекло время.
        # Каждый бафф хранится как {"id_баффа": timestamp_окончания}.
        # Если timestamp в прошлом (< time.time()) — бафф просрочен.
        # Внимание: чистим только в памяти, save() запишет изменения в БД.
        now = time.time()  # Текущее время в секундах (timestamp Unix)

        # Собираем ключи баффов, у которых время окончания меньше текущего
        expired = [k for k, v in user.active_buffs.items() if v < now]

        # Удаляем каждый истёкший бафф из словаря
        for k in expired:
            del user.active_buffs[k]

    async def save(self, user: UserProfile) -> None:
        # Записать профиль пользователя в БД.
        # Вызывается после любого изменения (баланс, позиция, баффы).
        # UPDATE ... WHERE id = — обновляем только одну строку по id.
        async with self._pool.acquire() as conn:   # Берём соединение из пула
            await conn.execute("""                   # Выполняем UPDATE
                UPDATE users SET
                    balance = $1,                    # Новый баланс
                    is_banned = $2,                  # Статус бана
                    last_lat = $3,                   # Последняя широта
                    last_lon = $4,                   # Последняя долгота
                    last_ping_time = $5,             # Время последнего пинга
                    total_distance_m = $6,           # Всего пройдено метров
                    active_buffs = $7::jsonb         # Баффы (превращаем словарь в JSONB)
                WHERE id = $8                        # Где id = этого пользователя
            """,
                user.balance,                        # $1
                user.is_banned,                      # $2
                user.last_lat,                       # $3
                user.last_lon,                       # $4
                user.last_ping_time,                 # $5
                user.total_distance_m,               # $6
                json.dumps(user.active_buffs),       # $7: словарь → JSON-строка → JSONB в БД
                user.id,                             # $8
            )

    async def update_position(
        self,
        user_id: int,
        lat: float,
        lon: float,
        distance_m: float | None = None,
    ) -> UserProfile:
        # Обновить GPS-позицию пользователя.
        user = await self.get_or_create(user_id)
        self._clean_expired_buffs(user)

        user.last_lat = lat
        user.last_lon = lon
        user.last_ping_time = time.time()

        if distance_m is not None:
            user.total_distance_m += distance_m

        await self.save(user)
        return user

    async def add_coins(self, user_id: int, amount: int) -> UserProfile | None:
        # Добавить монеты пользователю. Вернуть профиль или None, если пользователя нет.
        # Все операции — асинхронные: читаем из БД, меняем, пишем в БД.

        user = await self.get(user_id)  # Читаем профиль из БД (или None)
        if user is None:
            return None                  # Нет такого пользователя

        user.balance += amount           # Меняем баланс в памяти
        await self.save(user)            # Сохраняем изменения в БД
        return user

    async def deduct_coins(self, user_id: int, amount: int) -> UserProfile | None:
        # Списать монеты. Если не хватает — вернуть None.
        # Атомарная операция: проверка + списание (но без транзакции пока).

        user = await self.get(user_id)   # Читаем профиль из БД
        if user is None:
            return None                   # Нет пользователя
        if user.balance < amount:         # Не хватает денег
            return None

        user.balance -= amount            # Списываем в памяти
        await self.save(user)             # Сохраняем в БД
        return user

    async def buy_buff(self, user_id: int, buff_id: str) -> UserProfile | None:
        # Купить бафф (списать монеты и активировать на время).
        # Составная операция: проверить магазин → списать деньги → активировать.

        if buff_id not in BUFFS:          # Проверяем, существует ли такой бафф в магазине
            return None

        buff = BUFFS[buff_id]             # Берём описание баффа из словаря BUFFS
        user = await self.deduct_coins(user_id, buff["price"])
        # Пробуем списать цену баффа. Если не хватило — deduct_coins вернёт None.

        if user is None:                  # Если не смогли списать (нет денег)
            return None

        expiry = time.time() + buff["duration"]  # Время окончания = сейчас + длительность
        user.active_buffs[buff_id] = expiry      # Сохраняем в словаре активных баффов
        await self.save(user)                     # Пишем изменения в БД
        return user

    async def ban(self, user_id: int) -> UserProfile | None:
        # Забанить пользователя (поставить флаг is_banned = True).
        user = await self.get(user_id)   # Читаем профиль из БД
        if user is None:
            return None                   # Нет такого пользователя
        user.is_banned = True             # Баним в памяти
        await self.save(user)             # Сохраняем в БД
        return user


# Создаём единственный экземпляр хранилища (синглтон).
storage = PostgresStorage()
