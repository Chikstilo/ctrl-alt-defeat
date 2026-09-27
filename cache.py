import redis

from config import REDIS_DB, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT


pool = redis.ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    health_check_interval=30,
)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=pool)


def get_worker_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=30,
        socket_keepalive=True,
        health_check_interval=30,
    )
