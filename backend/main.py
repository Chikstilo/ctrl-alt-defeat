import json
import logging
import os
from contextlib import asynccontextmanager

import requests
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

import models  # noqa: F401 — регистрирует таблицы в metadata
from cache import get_redis
from config import ML_SERVICE_URL, NDTP_STREAM_KEY
from database import engine, get_db
from endpoints import dispatch, predictions, schedules, telemetry, vehicles, v1_dashboard
from schemas import HealthResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    get_redis().ping()
    logger.info("PostgreSQL и Redis доступны")
    yield
    logger.info("FastAPI останавливается")


app = FastAPI(
    title="MoscTrans Backend",
    version="1.0.0",
    description="Backend для телеметрии и прогноза задержек транспорта.",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(vehicles.router)
app.include_router(telemetry.router)
app.include_router(predictions.router)
app.include_router(schedules.router)
app.include_router(dispatch.router)
app.include_router(v1_dashboard.router)


@app.get("/", tags=["Health"])
def root() -> dict:
    return {
        "status": "ok",
        "message": "MoscTrans Backend is running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health(
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
) -> HealthResponse:
    postgres_ok = False
    redis_ok = False
    ml_ok = False
    queue_size = 0
    cached_routes = 0

    try:
        db.execute(text("SELECT 1"))
        postgres_ok = True
    except Exception:
        logger.exception("PostgreSQL health check failed")

    try:
        redis_client.ping()
        redis_ok = True
        queue_size = redis_client.xlen(NDTP_STREAM_KEY)
        cached_routes = sum(
            1 for _ in redis_client.scan_iter(match="route:*:vehicles", count=100)
        )
    except Exception:
        logger.exception("Redis health check failed")

    try:
        response = requests.get(f"{ML_SERVICE_URL.rstrip('/')}/health", timeout=2)
        ml_ok = response.status_code == 200
    except requests.RequestException:
        pass

    ndtp_tcp_ok = False
    try:
        if redis_ok:
            hb = redis_client.get("ndtp:heartbeat")
            ndtp_tcp_ok = hb is not None
    except Exception:
        logger.exception("Heartbeat check failed")

    return HealthResponse(
        status="ok" if postgres_ok and redis_ok else "degraded",
        postgres=postgres_ok,
        redis=redis_ok,
        ml_service=ml_ok,
        ndtp_tcp=ndtp_tcp_ok,
        queue_size=queue_size,
        cached_routes=cached_routes,
    )


@app.get("/worker/stats", tags=["Worker"])
def worker_stats(redis_client: Redis = Depends(get_redis)) -> dict:
    try:
        raw = redis_client.hgetall("worker:stats")
        queue_size = (
            redis_client.xlen(NDTP_STREAM_KEY)
            if redis_client.exists(NDTP_STREAM_KEY) else 0
        )
    except Exception:
        logger.exception("worker_stats failed")
        return {
            "processed": 0, "failed": 0, "skipped": 0,
            "fallback_used": 0, "crossings": 0,
            "last_update": None, "queue_size": 0,
        }

    return {
        "processed": int(raw.get("processed", 0)),
        "failed": int(raw.get("failed", 0)),
        "skipped": int(raw.get("skipped", 0)),
        "fallback_used": int(raw.get("fallback_used", 0)),
        "crossings": int(raw.get("crossings", 0)),
        "last_update": raw.get("last_update"),
        "queue_size": queue_size,
    }


@app.get("/routes", tags=["Routes"])
def list_routes(redis_client: Redis = Depends(get_redis)) -> dict:
    """Список маршрутов с агрегированной статистикой по кэшу прогнозов."""
    routes = set()
    for key in redis_client.scan_iter(match="route:*:vehicles", count=100):
        parts = key.split(":")
        if len(parts) >= 2 and parts[1]:
            routes.add(parts[1])

    result = []
    for route_id in sorted(routes):
        cache_key = f"route:{route_id}:vehicles"
        items = redis_client.zrange(cache_key, 0, -1)

        delays: list[int] = []
        risks: list[str] = []
        for raw in items:
            try:
                record = json.loads(raw)
                delays.append(record.get("delay_seconds", 0))
                risks.append(record.get("risk_level", "low"))
            except json.JSONDecodeError:
                continue

        avg_delay = round(sum(delays) / len(delays), 1) if delays else 0.0
        max_risk = (
            "high" if "high" in risks
            else "medium" if "medium" in risks
            else "low"
        )

        result.append({
            "route_id": route_id,
            "vehicles_count": len(items),
            "avg_delay_seconds": avg_delay,
            "max_risk": max_risk,
        })

    return {"count": len(result), "routes": result}