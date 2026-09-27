import logging

from fastapi import APIRouter, Depends, HTTPException
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from auth import require_token
from cache import get_redis
from config import NDTP_STREAM_KEY
from database import get_db
from models import TelemetryRecord
from schemas import TelemetryIn, TelemetryOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


def _stream_fields(payload: TelemetryIn) -> dict[str, str]:
    return {
        "vehicle_id": payload.vehicle_id,
        "route_id": payload.route_id or "",
        "lat": str(payload.lat),
        "lon": str(payload.lon),
        "alt": "" if payload.alt is None else str(payload.alt),
        "speed": "" if payload.speed is None else str(payload.speed),
        "heading": "" if payload.heading is None else str(payload.heading),
        "location_valid": (
            "" if payload.location_valid is None
            else str(int(payload.location_valid))
        ),
        "stop_id": payload.stop_id or "",
        "dwell_time_seconds": (
            "" if payload.dwell_time_seconds is None
            else str(payload.dwell_time_seconds)
        ),
        "event_time": payload.event_time.isoformat(),
    }


@router.post(
    "/",
    response_model=TelemetryOut,
    status_code=201,
    dependencies=[Depends(require_token)],
)
def ingest_telemetry(
    payload: TelemetryIn,
    db: Session = Depends(get_db),
    r: Redis = Depends(get_redis),
) -> TelemetryRecord:
    record = TelemetryRecord(
        vehicle_id=payload.vehicle_id,
        route_id=payload.route_id,
        lat=payload.lat,
        lon=payload.lon,
        alt=payload.alt,
        speed=payload.speed,
        heading=payload.heading,
        location_valid=payload.location_valid,
        stop_id=payload.stop_id,
        dwell_time_seconds=payload.dwell_time_seconds,
        event_time=payload.event_time,
    )

    db.add(record)
    try:
        db.commit()
        db.refresh(record)
    except Exception:
        db.rollback()
        logger.exception("Не удалось сохранить телеметрию")
        raise HTTPException(
            status_code=500,
            detail="Не удалось сохранить телеметрию.",
        )

    try:
        r.xadd(
            NDTP_STREAM_KEY,
            _stream_fields(payload),
            maxlen=10_000,
            approximate=True,
        )
    except RedisError:
        logger.exception(
            "Телеметрия id=%s сохранена в БД, но не попала в Redis Stream",
            record.id,
        )

    return record


@router.get("/{vehicle_id}", response_model=list[TelemetryOut])
def get_vehicle_history(
    vehicle_id: str,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[TelemetryRecord]:
    if limit < 1 or limit > 10_000:
        raise HTTPException(
            status_code=422,
            detail="limit должен быть в диапазоне от 1 до 10000.",
        )
    return (
        db.query(TelemetryRecord)
        .filter(TelemetryRecord.vehicle_id == vehicle_id)
        .order_by(TelemetryRecord.event_time.desc())
        .limit(limit)
        .all()
    )