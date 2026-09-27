import json
import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

import requests
from redis.exceptions import ResponseError
from sqlalchemy.exc import IntegrityError

from cache import get_worker_redis
from config import (
    ML_SERVICE_URL,
    NDTP_CONSUMER_GROUP,
    NDTP_STREAM_KEY,
    PREDICTION_CACHE_TTL,
    SCHEDULE_MATCH_WINDOW_MINUTES,
)
from database import SessionLocal
from feature_builder import FeatureBuilder
from models import ProcessedStreamMessage, Prediction, Schedule, StopCrossing

logger = logging.getLogger("worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}"
stats = {
    "processed": 0,
    "failed": 0,
    "skipped": 0,
    "fallback_used": 0,
    "crossings": 0,
}

MSK_TZ = timezone(timedelta(hours=3))

DEBUG_LOG_FEATURES = os.getenv("DEBUG_LOG_FEATURES", "0") == "1"
DEBUG_SAVE_FEATURES = os.getenv("DEBUG_SAVE_FEATURES", "0") == "1"
DEBUG_FEATURES_TTL = 3600  
FEATURE_BUILDER = FeatureBuilder()

_SCHEDULE_CACHE: dict[str, tuple[float, list[dict]]] = {}
SCHEDULE_CACHE_TTL = 300 

STOP_PROXIMITY_THRESHOLD_M = 1500.0

CROSSING_TIME_WINDOW = timedelta(minutes=30)

_passed_stops: dict[str, set[tuple[str, str]]] = {}
_PASSED_STOPS_MAX = 500

RADIUS_EARTH_M = 6_371_000


def haversine_m(lon1, lat1, lon2, lat2) -> float:
    try:
        if any(v is None for v in [lon1, lat1, lon2, lat2]):
            return float("inf")
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
        dlon, dlat = lon2 - lon1, lat2 - lat1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return 2 * RADIUS_EARTH_M * asin(sqrt(a))
    except Exception:
        return float("inf")

def ensure_group(redis_client) -> None:
    try:
        redis_client.xgroup_create(
            NDTP_STREAM_KEY, NDTP_CONSUMER_GROUP, id="0-0", mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=MSK_TZ)
    return result

def schedule_delay(route_id, event_time):
    if not route_id:
        return None
    start = event_time - timedelta(minutes=SCHEDULE_MATCH_WINDOW_MINUTES)
    end = event_time + timedelta(minutes=SCHEDULE_MATCH_WINDOW_MINUTES)
    db = SessionLocal()
    try:
        schedule = (
            db.query(Schedule)
            .filter(
                Schedule.route_id == route_id,
                Schedule.scheduled_arrival >= start,
                Schedule.scheduled_arrival <= end,
            )
            .order_by(Schedule.scheduled_arrival)
            .first()
        )
        if schedule is None:
            return None
        return (event_time - schedule.scheduled_arrival).total_seconds()
    finally:
        db.close()


def load_schedule_rows(route_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Schedule)
            .filter(Schedule.route_id == route_id)
            .order_by(Schedule.scheduled_arrival)
            .all()
        )
        return [
            {
                "stop_id": r.stop_id,
                "scheduled_arrival": (
                    r.scheduled_arrival.astimezone(MSK_TZ).replace(tzinfo=None)
                    if r.scheduled_arrival.tzinfo else r.scheduled_arrival
                ),
                "lat": r.lat if r.lat is not None else float("nan"),
                "lon": r.lon if r.lon is not None else float("nan"),
            }
            for r in rows
        ]
    finally:
        db.close()


def load_schedule_rows_cached(route_id: str) -> list[dict]:
    now = time.time()
    cached = _SCHEDULE_CACHE.get(route_id)
    if cached and now - cached[0] < SCHEDULE_CACHE_TTL:
        return cached[1]
    rows = load_schedule_rows(route_id)
    _SCHEDULE_CACHE[route_id] = (now, rows)
    return rows


def load_recent_crossings(vehicle_id: str, limit: int = 50) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(StopCrossing)
            .filter(StopCrossing.vehicle_id == vehicle_id)
            .order_by(StopCrossing.actual_time.desc())
            .limit(limit)
            .all()
        )
        return [
            {"delay_seconds": float(r.delay_seconds), "actual_time": r.actual_time}
            for r in rows
        ]
    finally:
        db.close()


def detect_stop_crossing(
    vehicle_id: str,
    route_id: str | None,
    lat: float,
    lon: float,
    event_time: datetime,
    schedule: list[dict],
) -> dict | None:
    if not route_id or not schedule:
        return None

    if event_time.tzinfo is not None:
        event_msk = event_time.astimezone(MSK_TZ).replace(tzinfo=None)
    else:
        event_msk = event_time

    candidates = []
    for s in schedule:
        s_lat = s.get("lat")
        s_lon = s.get("lon")
        if s_lat is None or s_lon is None:
            continue
        # отсеиваем NaN
        if isinstance(s_lat, float) and s_lat != s_lat:
            continue
        if isinstance(s_lon, float) and s_lon != s_lon:
            continue
        candidates.append(s)

    if not candidates:
        return None

    best = None
    best_dist = float("inf")
    for stop in candidates:
        dist = haversine_m(lon, lat, stop["lon"], stop["lat"])
        if dist < best_dist:
            best_dist = dist
            best = stop

    if best is None or best_dist > STOP_PROXIMITY_THRESHOLD_M:
        return None

    planned_iso = best["scheduled_arrival"].isoformat()
    key = (best["stop_id"], planned_iso)
    passed = _passed_stops.setdefault(vehicle_id, set())
    if key in passed:
        return None

    if len(passed) > _PASSED_STOPS_MAX:
        passed.clear()

    passed.add(key)

    delay = (event_msk - best["scheduled_arrival"]).total_seconds()

    return {
        "vehicle_id": vehicle_id,
        "route_id": route_id,
        "stop_id": best["stop_id"],
        "planned_time": best["scheduled_arrival"].replace(tzinfo=MSK_TZ),
        "actual_time": event_msk.replace(tzinfo=MSK_TZ),
        "delay_seconds": delay,
        "distance_m": best_dist,
    }


def save_crossing(crossing: dict) -> bool:

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    db = SessionLocal()
    try:
        statement = (
            pg_insert(StopCrossing)
            .values(
                vehicle_id=crossing["vehicle_id"],
                route_id=crossing["route_id"],
                stop_id=crossing["stop_id"],
                planned_time=crossing["planned_time"],
                actual_time=crossing["actual_time"],
                delay_seconds=crossing["delay_seconds"],
            )
           .on_conflict_do_nothing(
    constraint="uq_stop_crossing_vehicle_stop_planned",
)
        )
        result = db.execute(statement)
        db.commit()

        if result.rowcount and result.rowcount > 0:
            logger.info(
                "CROSSING vehicle=%s stop=%s delay=%.1fs dist=%.0fm",
                crossing["vehicle_id"],
                crossing["stop_id"],
                crossing["delay_seconds"],
                crossing.get("distance_m", 0.0),
            )
            return True
        return False
    except Exception:
        db.rollback()
        logger.exception("Ошибка сохранения stop_crossing")
        return False
    finally:
        db.close()

def predict_via_ml(features: dict) -> dict | None:

    import numpy as np

    clean_features = {
        k: (None if isinstance(v, float) and np.isnan(v) else v)
        for k, v in features.items()
    }

    for attempt in range(3):
        try:
            response = requests.post(
                f"{ML_SERVICE_URL.rstrip('/')}/predict",
                json={"features": clean_features},
                timeout=5,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                logger.warning(
                    "ML-сервис: попытка %d не удалась (%s), повтор",
                    attempt + 1, type(e).__name__,
                )

    logger.exception("ML-сервис недоступен после 3 попыток; fallback")
    return None


def fallback_predict(data: dict, delay: float | None, event_time: datetime) -> dict:
    """Эвристика, если ML не ответил."""
    speed = float(data.get("speed") or 0)
    dwell = float(data.get("dwell_time_seconds") or 0)

    if delay is not None:
        delay_seconds = int(round(delay))
    elif speed < 15 or dwell > 120:
        delay_seconds = 720
    elif speed < 30:
        delay_seconds = 420
    else:
        delay_seconds = 120

    delay_seconds = max(-600, min(3600, delay_seconds))

    if delay_seconds > 600:
        risk, prob = "high", 85.0
        recommendation = "Выпустить дополнительный ТС на линию"
    elif delay_seconds > 300:
        risk, prob = "medium", 60.0
        recommendation = "Сократить время стоянки на остановках"
    else:
        risk, prob = "low", 20.0
        recommendation = "Задержка в пределах нормы"

    now_msk = (
        event_time.astimezone(MSK_TZ) if event_time.tzinfo
        else event_time.replace(tzinfo=MSK_TZ)
    )
    arrival_time = now_msk + timedelta(seconds=delay_seconds)

    return {
        "delay_seconds": delay_seconds,
        "probability": prob,
        "risk_level": risk,
        "scheduled_arrival": now_msk.isoformat(),
        "arrival_time": arrival_time.isoformat(),
        "recommendation": recommendation,
    }


def process_message(redis_client, message_id: str, data: dict) -> bool:
    vehicle_id = data.get("vehicle_id")
    if not vehicle_id:
        stats["skipped"] += 1
        return True

    try:
        route_id = data.get("route_id") or None
        event_time = parse_datetime(
            data.get("event_time") or datetime.now(timezone.utc).isoformat()
        )

        lat = float(data["lat"])
        lon = float(data["lon"])
        speed = float(data.get("speed") or 0)
        heading = float(data.get("heading") or 0)

        event_time_msk = (
            event_time.astimezone(MSK_TZ).replace(tzinfo=None)
            if event_time.tzinfo is not None else event_time
        )
        if route_id:
            FEATURE_BUILDER.add_telemetry(
                vehicle_id=str(vehicle_id),
                route_id=route_id,
                event_time=event_time_msk,
                lat=lat, lon=lon, speed=speed, heading=heading,
            )

        schedule = load_schedule_rows_cached(route_id) if route_id else []

        if route_id and schedule:
            try:
                crossing = detect_stop_crossing(
                    vehicle_id=str(vehicle_id),
                    route_id=route_id,
                    lat=lat, lon=lon,
                    event_time=event_time,
                    schedule=schedule,
                )
                if crossing:
                    if save_crossing(crossing):
                        stats["crossings"] += 1
            except Exception:
                logger.exception("Ошибка детекции crossing")

        crossings_history = (
            load_recent_crossings(str(vehicle_id), limit=50)
            if route_id else []
        )

        delay = schedule_delay(route_id, event_time)

        prediction = None
        if route_id and schedule:
            current = {
                "lat": lat, "lon": lon,
                "speed": speed, "heading": heading,
                "event_time": event_time,
            }
            features = FEATURE_BUILDER.build(
                vehicle_id=str(vehicle_id),
                route_id=route_id,
                T=event_time,
                current=current,
                schedule=schedule,
                crossings=crossings_history,
            )
            if features:
                if DEBUG_LOG_FEATURES:
                    try:
                        logger.info(
                            "FEATURES vehicle=%s route=%s features=%s",
                            vehicle_id, route_id,
                            json.dumps(features, default=str, ensure_ascii=False),
                        )
                    except Exception:
                        logger.exception("Не удалось залогировать фичи")

                if DEBUG_SAVE_FEATURES:
                    try:
                        debug_key = f"debug:features:{vehicle_id}"
                        redis_client.hset(
                            debug_key,
                            message_id,
                            json.dumps({
                                "input": {
                                    "lat": lat, "lon": lon,
                                    "speed": speed, "heading": heading,
                                    "event_time": event_time.isoformat(),
                                    "route_id": route_id,
                                    "vehicle_id": str(vehicle_id),
                                },
                                "features": features,
                            }, default=str, ensure_ascii=False),
                        )
                        redis_client.expire(debug_key, DEBUG_FEATURES_TTL)
                    except Exception:
                        logger.exception("Не удалось сохранить фичи в Redis")

                prediction = predict_via_ml(features)

        if prediction is None:
            stats["fallback_used"] += 1
            prediction = fallback_predict(data, delay, event_time)

        delay_seconds = max(-600, min(3600, int(prediction["delay_seconds"])))
        probability = float(prediction["probability"])
        risk_level = prediction["risk_level"]
        recommendation = prediction.get("recommendation")

        scheduled_arrival = None
        if prediction.get("scheduled_arrival"):
            scheduled_arrival = parse_datetime(prediction["scheduled_arrival"])

        arrival_time = None
        if prediction.get("arrival_time"):
            arrival_time = parse_datetime(prediction["arrival_time"])
        if arrival_time is None:
            arrival_time = datetime.now(timezone.utc) + timedelta(minutes=15)

        record = {
            "vehicle_id": str(vehicle_id),
            "route_id": route_id or "unknown",
            "delay_seconds": delay_seconds,
            "probability": probability,
            "risk_level": risk_level,
            "scheduled_arrival": (
                scheduled_arrival.isoformat() if scheduled_arrival else None
            ),
            "arrival_time": arrival_time.isoformat(),
            "recommendation": recommendation,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        db = SessionLocal()
        try:
            db.add(ProcessedStreamMessage(stream_id=f"{NDTP_STREAM_KEY}/{message_id}"))
            db.add(Prediction(
                vehicle_id=record["vehicle_id"],
                route_id=record["route_id"],
                delay_seconds=record["delay_seconds"],
                probability=record["probability"],
                risk_level=record["risk_level"],
                scheduled_arrival=scheduled_arrival,
                arrival_time=arrival_time,
                recommendation=record["recommendation"],
            ))
            db.commit()
        except IntegrityError:
            db.rollback()
            stats["skipped"] += 1
            return True
        finally:
            db.close()

        cache_key = f"route:{record['route_id']}:vehicles"
        for old_raw in redis_client.zrange(cache_key, 0, -1):
            try:
                old = json.loads(old_raw)
                if old.get("vehicle_id") == record["vehicle_id"]:
                    redis_client.zrem(cache_key, old_raw)
            except json.JSONDecodeError:
                redis_client.zrem(cache_key, old_raw)

        redis_client.zadd(
            cache_key,
            {json.dumps(record, ensure_ascii=False): time.time()},
        )
        redis_client.expire(cache_key, PREDICTION_CACHE_TTL)

        stats["processed"] += 1
        return True

    except Exception:
        logger.exception("Ошибка обработки stream message %s", message_id)
        stats["failed"] += 1
        return False


def reclaim_pending(redis_client) -> None:
    cursor = "0-0"
    while True:
        result = redis_client.xautoclaim(
            NDTP_STREAM_KEY, NDTP_CONSUMER_GROUP, CONSUMER_NAME,
            min_idle_time=60_000, start_id=cursor, count=20,
        )
        cursor, messages = result[0], result[1]
        for message_id, data in messages:
            if process_message(redis_client, message_id, data):
                redis_client.xack(NDTP_STREAM_KEY, NDTP_CONSUMER_GROUP, message_id)
        if cursor == "0-0":
            break


def main() -> None:
    redis_client = get_worker_redis()
    ensure_group(redis_client)
    logger.info("Worker слушает stream=%s", NDTP_STREAM_KEY)
    if DEBUG_LOG_FEATURES:
        logger.info("DEBUG_LOG_FEATURES включён")
    if DEBUG_SAVE_FEATURES:
        logger.info("DEBUG_SAVE_FEATURES включён")

    last_reclaim = 0.0
    try:
        while True:
            try:
                now = time.monotonic()
                if now - last_reclaim >= 30:
                    reclaim_pending(redis_client)
                    last_reclaim = now

                batches = redis_client.xreadgroup(
                    groupname=NDTP_CONSUMER_GROUP,
                    consumername=CONSUMER_NAME,
                    streams={NDTP_STREAM_KEY: ">"},
                    count=10,
                    block=1000,
                )
                for _, messages in batches:
                    for message_id, data in messages:
                        if process_message(redis_client, message_id, data):
                            redis_client.xack(
                                NDTP_STREAM_KEY, NDTP_CONSUMER_GROUP, message_id,
                            )

                redis_client.hset(
                    "worker:stats",
                    mapping={
                        **{k: str(v) for k, v in stats.items()},
                        "last_update": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                logger.exception("Ошибка Redis/worker; повтор через 2 секунды")
                time.sleep(2)
    except KeyboardInterrupt:
        logger.info("Worker остановлен")
    finally:
        try:
            redis_client.hset(
                "worker:stats",
                mapping={
                    **{k: str(v) for k, v in stats.items()},
                    "last_update": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info("Статистика сохранена")
        except Exception:
            pass

if __name__ == "__main__":
    main()