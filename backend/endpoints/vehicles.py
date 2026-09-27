"""Привязка NDTP unit_id к vehicle_id и route_id."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import require_token
from database import get_db
from models import VehicleRoute
from schemas import VehicleRouteIn, VehicleRouteOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])


@router.post(
    "/",
    response_model=VehicleRouteOut,
    dependencies=[Depends(require_token)],
)
def set_vehicle_route(
    payload: VehicleRouteIn,
    db: Session = Depends(get_db),
) -> VehicleRoute:
    """Создать или обновить привязку NDTP-устройства к маршруту."""
    record = db.scalar(
        select(VehicleRoute).where(VehicleRoute.unit_id == payload.unit_id)
    )

    if record is None:
        record = VehicleRoute(
            unit_id=payload.unit_id,
            vehicle_id=payload.vehicle_id,
            route_id=payload.route_id,
            active=payload.active,
        )
        db.add(record)
    else:
        record.vehicle_id = payload.vehicle_id
        record.route_id = payload.route_id
        record.active = payload.active

    try:
        db.commit()
        db.refresh(record)
    except IntegrityError as exc:
        db.rollback()
        logger.warning("Не удалось сохранить привязку unit_id=%s", payload.unit_id)
        raise HTTPException(
            status_code=409,
            detail="Этот vehicle_id уже привязан к другому устройству.",
        ) from exc

    return record


@router.get("/", response_model=list[VehicleRouteOut])
def list_vehicle_routes(
    active_only: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> list[VehicleRoute]:
    query = select(VehicleRoute).order_by(VehicleRoute.unit_id)
    if active_only:
        query = query.where(VehicleRoute.active.is_(True))
    return list(db.scalars(query).all())


@router.get("/{unit_id}", response_model=VehicleRouteOut)
def get_vehicle_route(
    unit_id: int,
    db: Session = Depends(get_db),
) -> VehicleRoute:
    record = db.scalar(
        select(VehicleRoute).where(VehicleRoute.unit_id == unit_id)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено.")
    return record


@router.delete("/{unit_id}", dependencies=[Depends(require_token)])
def deactivate_vehicle_route(
    unit_id: int,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Мягко деактивировать устройство."""
    record = db.scalar(
        select(VehicleRoute).where(VehicleRoute.unit_id == unit_id)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено.")

    record.active = False
    db.commit()
    return {"status": "ok", "message": "Привязка отключена."}