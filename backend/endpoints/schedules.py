from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from auth import require_token
from database import get_db
from models import Schedule
from schemas import ScheduleIn, ScheduleOut

router = APIRouter(prefix="/schedules", tags=["Schedules"])


@router.post("/", status_code=201, dependencies=[Depends(require_token)])
def upload_schedule(
    payload: list[ScheduleIn],
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    if not payload:
        raise HTTPException(status_code=422, detail="Список расписания пуст.")

    rows = [
        {
            "route_id": item.route_id,
            "stop_id": item.stop_id,
            "stop_name": item.stop_name,
            "scheduled_arrival": item.scheduled_arrival,
            "lat": item.lat,
            "lon": item.lon,
        }
        for item in payload
    ]

    statement = (
        insert(Schedule)
        .values(rows)
        .on_conflict_do_nothing(
            constraint="uq_schedule_route_stop_arrival",
        )
    )

    result = db.execute(statement)
    db.commit()

    return {
        "status": "ok",
        "received": len(rows),
        "inserted": result.rowcount or 0,
        "duplicates_skipped": len(rows) - (result.rowcount or 0),
    }


@router.get("/{route_id}", response_model=list[ScheduleOut])
def get_schedule(
    route_id: str,
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
) -> list[Schedule]:
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=hours)

    query = (
        select(Schedule)
        .where(
            Schedule.route_id == route_id,
            Schedule.scheduled_arrival >= now,
            Schedule.scheduled_arrival <= until,
        )
        .order_by(Schedule.scheduled_arrival)
    )
    return list(db.scalars(query).all())


@router.delete("/{route_id}", dependencies=[Depends(require_token)])
def delete_schedule(
    route_id: str,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    deleted = (
        db.query(Schedule)
        .filter(Schedule.route_id == route_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"status": "ok", "deleted": deleted}