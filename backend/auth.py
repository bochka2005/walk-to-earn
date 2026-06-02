# ═══════════════════════════════════════════════════════════════
# Файл: auth.py
# Назначение: Проверка подлинности данных от Telegram WebApp.
# Что происходит: Telegram присылает строку initData, в которой
# есть хэш. Мы пересчитываем хэш на своей стороне (HMAC-SHA256)
# с секретным токеном бота. Если хэши совпали — данные настоящие,
# можно доставать ID пользователя.
# ═══════════════════════════════════════════════════════════════

import hashlib          # Функции для хэширования (SHA256)
import hmac             # Алгоритм HMAC — хэш с секретным ключом
import json             # Работа с JSON (разбираем строку с данными пользователя)
from urllib.parse import parse_qsl  # Разбирает строку вида "ключ=знач&ключ2=знач2"

from backend.config import BOT_TOKEN  # Берём токен бота из config.py


def validate_init_data(init_data: str) -> dict | None:
    # Проверить initData от Telegram и вернуть разобранные данные.
    # Если проверка не прошла — вернуть None.

    # parse_qsl превращает строку "a=1&b=2" в список [("a","1"), ("b","2")]
    # dict(...) превращает список в словарь {"a": "1", "b": "2"}
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))

    # Извлекаем поле "hash" из словаря и удаляем его (pop).
    # hash — это подпись, присланная Telegram.
    hash_value = parsed.pop("hash", None)

    # Если хэша нет — данные явно подделаны, возвращаем None
    if hash_value is None:
        return None

    # Сортируем оставшиеся пары ключ=значение по алфавиту (как требует Telegram)
    items = sorted(parsed.items())

    # Склеиваем их через \n: "ключ1=знач1\nключ2=знач2\n..."
    # Именно из этой строки Telegram считал хэш на своей стороне.
    data_check_string = "\n".join(f"{k}={v}" for k, v in items)

    # Создаём секретный ключ: HMAC-SHA256 от строки "WebAppData"
    # с ключом = токен бота.
    # .digest() возвращает байты (бинарные данные), не hex-строку.
    secret_key = hmac.new(
        b"WebAppData",          # Строка "WebAppData" как байты
        BOT_TOKEN.encode(),     # Токен бота как байты
        hashlib.sha256          # Алгоритм хэширования
    ).digest()

    # Считаем хэш от нашей data_check_string с помощью secret_key
    calculated_hash = hmac.new(
        secret_key,                     # Ключ, который мы создали выше
        data_check_string.encode(),     # Сама строка данных как байты
        hashlib.sha256                  # Алгоритм
    ).hexdigest()                       # hexdigest() — строка из 64 hex-символов

    # Сравниваем наш хэш с тем, что прислал Telegram.
    if calculated_hash != hash_value:
        return None          # Не совпали — данные подделаны

    # Всё хорошо — возвращаем разобранные данные
    return parsed


def extract_user_id(parsed: dict) -> int | None:
    # Из разобранных initData достать Telegram ID пользователя.
    # ID лежит внутри JSON-поля "user": {"id": 123, "first_name": "..."}

    # Берём сырое значение поля "user" (это JSON-строка)
    user_raw = parsed.get("user")

    # Если поля нет — вернуть None
    if user_raw is None:
        return None

    try:
        # Если user — строка, разбираем JSON (json.loads).
        # Если уже объект (dict) — используем как есть.
        user = json.loads(user_raw) if isinstance(user_raw, str) else user_raw

        # Достаём "id" из объекта пользователя и превращаем в int
        return int(user["id"])

    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # Если что-то пошло не так:
        # JSONDecodeError — невалидный JSON
        # KeyError — нет поля "id"
        # TypeError, ValueError — странный тип данных
        return None
