import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from sqlalchemy.orm import Session

from cache import get_redis
from database import get_db
from models import Prediction
from schemas import PredictionOut, RoutePredictionsOut

router = APIRouter(prefix="/predictions", tags=["Predictions"])

MSK_TZ = timezone(timedelta(hours=3))


def parse_dt(value) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK_TZ)
    return dt.astimezone(MSK_TZ)


def parse_cached_prediction(raw: str) -> PredictionOut:
    data = json.loads(raw)

    # Обязательное поле
    data["arrival_time"] = parse_dt(data.get("arrival_time"))
    if data["arrival_time"] is None:
        # Если по какой-то причине пусто — подставим now+15 мин
        data["arrival_time"] = datetime.now(MSK_TZ) + timedelta(minutes=15)

    # Опциональное
    if data.get("scheduled_arrival"):
        data["scheduled_arrival"] = parse_dt(data["scheduled_arrival"])

    if data.get("updated_at"):
        data["updated_at"] = parse_dt(data["updated_at"])

    return PredictionOut(**data)


@router.get("/{route_id}/latest", response_model=PredictionOut)
def get_latest_prediction(
    route_id: str,
    redis_client: Redis = Depends(get_redis),
) -> PredictionOut:
    items = redis_client.zrevrange(f"route:{route_id}:vehicles", 0, 0)
    if not items:
        raise HTTPException(404, "Для маршрута пока нет прогноза.")
    return parse_cached_prediction(items[0])


@router.get("/{route_id}/history")
def get_route_history(
    route_id: str,
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=1000, ge=1, le=10_000),
    db: Session = Depends(get_db),
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    records = (
        db.query(Prediction)
        .filter(
            Prediction.route_id == route_id,
            Prediction.created_at >= since,
        )
        .order_by(Prediction.created_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "route_id": route_id,
        "count": len(records),
        "records": [
            {
                "id": item.id,
                "vehicle_id": item.vehicle_id,
                "delay_seconds": item.delay_seconds,
                "probability": item.probability,
                "risk_level": item.risk_level,
                "scheduled_arrival": (
                    item.scheduled_arrival.isoformat()
                    if item.scheduled_arrival else None
                ),
                "arrival_time": (
                    item.arrival_time.isoformat()
                    if item.arrival_time else None
                ),
                "recommendation": item.recommendation,
                "created_at": item.created_at.isoformat(),
            }
            for item in records
        ],
    }


@router.get("/{route_id}", response_model=RoutePredictionsOut)
def get_route_predictions(
    route_id: str,
    redis_client: Redis = Depends(get_redis),
) -> RoutePredictionsOut:
    items = redis_client.zrevrange(f"route:{route_id}:vehicles", 0, -1)
    vehicles = []

    for item in items:
        try:
            vehicles.append(parse_cached_prediction(item))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue

    if not vehicles:
        raise HTTPException(404, "Для маршрута пока нет прогнозов.")

    return RoutePredictionsOut(
        route_id=route_id,
        count=len(vehicles),
        vehicles=vehicles,
    )