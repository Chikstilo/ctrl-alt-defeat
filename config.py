import os

from dotenv import load_dotenv

load_dotenv()


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = env_int("DB_PORT", 5432)
DB_NAME = os.getenv("DB_NAME", "postgres")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = env_int("REDIS_PORT", 6379)
REDIS_DB = env_int("REDIS_DB", 0)
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://localhost:8002")
NDTP_TCP_PORT = env_int("NDTP_TCP_PORT", 9201)

NDTP_STREAM_KEY = os.getenv("NDTP_STREAM_KEY", "ndtp:stream")
NDTP_CONSUMER_GROUP = os.getenv("NDTP_CONSUMER_GROUP", "telemetry-workers")

PREDICTION_CACHE_TTL = env_int("PREDICTION_CACHE_TTL", 900)
SCHEDULE_MATCH_WINDOW_MINUTES = env_int(
    "SCHEDULE_MATCH_WINDOW_MINUTES",
    30,
)
