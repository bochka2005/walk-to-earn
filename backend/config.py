from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

ADMIN_IDS: list[int] = [715136637]

MIN_PING_INTERVAL_S: int = 10

SPEED_LIMIT_KMH: float = 15.0

METERS_PER_COIN: int = 10

DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://walkuser:walkpass@localhost:5432/walktoearn")
