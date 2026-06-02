import time
from dataclasses import dataclass

import asyncpg

from config import DATABASE_URL


@dataclass
class UserProfile:
    id: int
    balance: int = 0
    is_banned: bool = False
    last_lat: float | None = None
    last_lon: float | None = None
    last_ping_time: float | None = None
    total_distance_m: float = 0.0


async def row_to_profile(row: asyncpg.Record) -> UserProfile:
    return UserProfile(
        id=row["id"],
        balance=row["balance"],
        is_banned=row["is_banned"],
        last_lat=row["last_lat"],
        last_lon=row["last_lon"],
        last_ping_time=row["last_ping_time"],
        total_distance_m=row["total_distance_m"],
    )


class PostgresStorage:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id              BIGINT PRIMARY KEY,
                    balance         INTEGER NOT NULL DEFAULT 0,
                    is_banned       BOOLEAN NOT NULL DEFAULT FALSE,
                    last_lat        DOUBLE PRECISION,
                    last_lon        DOUBLE PRECISION,
                    last_ping_time  DOUBLE PRECISION,
                    total_distance_m DOUBLE PRECISION NOT NULL DEFAULT 0.0
                )
            """)

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def get_or_create(self, user_id: int) -> UserProfile:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO users (id) VALUES ($1)
                ON CONFLICT (id) DO NOTHING
                RETURNING *
            """, user_id)
            if row is None:
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE id = $1", user_id
                )
        return await row_to_profile(row)

    async def save(self, user: UserProfile) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE users SET
                    balance = $1,
                    is_banned = $2,
                    last_lat = $3,
                    last_lon = $4,
                    last_ping_time = $5,
                    total_distance_m = $6
                WHERE id = $7
            """,
                user.balance,
                user.is_banned,
                user.last_lat,
                user.last_lon,
                user.last_ping_time,
                user.total_distance_m,
                user.id,
            )

    async def update_position(
        self,
        user_id: int,
        lat: float,
        lon: float,
        distance_m: float = 0.0,
        coins_earned: int = 0,
    ) -> UserProfile:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE users SET
                    balance = balance + $1,
                    last_lat = $2,
                    last_lon = $3,
                    last_ping_time = $4,
                    total_distance_m = total_distance_m + $5
                WHERE id = $6
                RETURNING *
            """, coins_earned, lat, lon, time.time(), distance_m, user_id)
        return await row_to_profile(row)

    async def ban(self, user_id: int) -> UserProfile | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE users SET is_banned = TRUE WHERE id = $1 RETURNING *
            """, user_id)
        if row is None:
            return None
        return await row_to_profile(row)


storage = PostgresStorage()
