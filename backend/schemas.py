# ═══════════════════════════════════════════════════════════════
# Файл: schemas.py
# Назначение: Описание форматов данных (схем), которые сервер
# принимает от фронтенда.
# Что происходит: Pydantic проверяет входящие JSON — если поля
# не совпадают, FastAPI сам вернёт ошибку 422 (неверный формат).
# ═══════════════════════════════════════════════════════════════

from pydantic import BaseModel, Field  # BaseModel — базовый класс для схем,
                                       # Field — дополнительные правила для поля


class WalkPingRequest(BaseModel):
    # Схема для POST-запроса на /walk/ping (отправка GPS-координат)
    init_data: str     # initData от Telegram WebApp (для проверки подлинности)
    lat: float = Field(..., ge=-90, le=90)      # Широта (от -90 до 90)
    lon: float = Field(..., ge=-180, le=180)    # Долгота (от -180 до 180)


class AdminConsoleRequest(BaseModel):
    # Схема для POST-запроса на /admin/console (админ-команды)
    init_data: str     # initData от Telegram WebApp
    command: str       # Текст команды (например "/addmoney 123 500")


class ShopBuyRequest(BaseModel):
    # Схема для POST-запроса на /shop/buy (покупка баффа)
    init_data: str     # initData от Telegram WebApp
    item_id: str       # ID предмета (например "x2_coins" или "speed_boost")
