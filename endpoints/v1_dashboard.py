import json
import logging
import time

from fastapi import APIRouter, Depends
from redis import Redis
from sqlalchemy import func
from sqlalchemy.orm import Session

from cache import get_redis
from database import get_db
from models import Schedule, TelemetryRecord

logger = logging.getLogger("v1_dashboard")
router = APIRouter(prefix="/api/v1", tags=["Dashboard v1"])

ACK_KEY = "dashboard:acked_incidents"
ACK_TTL_SECONDS = 3600


# ---------------------------------------------------------------- helpers

def _get_cached_predictions(redis_client: Redis) -> dict[str, list[dict]]:
    by_route: dict[str, list[dict]] = {}
    try:
        for key in redis_client.scan_iter(match="route:*:vehicles", count=100):
            parts = key.split(":")
            if len(parts) < 3:
                continue
            route_id = parts[1]
            records = []
            for raw in redis_client.zrange(key, 0, -1):
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
            if records:
                by_route[route_id] = records
    except Exception:
        logger.exception("scan_iter failed")
    return by_route


def _get_last_positions(db: Session, vehicle_ids: list[str]) -> dict[str, dict]:
    if not vehicle_ids:
        return {}
    subq = (
        db.query(
            TelemetryRecord.vehicle_id,
            func.max(TelemetryRecord.event_time).label("max_time"),
        )
        .filter(TelemetryRecord.vehicle_id.in_(vehicle_ids))
        .group_by(TelemetryRecord.vehicle_id)
        .subquery()
    )
    rows = (
        db.query(TelemetryRecord)
        .join(
            subq,
            (TelemetryRecord.vehicle_id == subq.c.vehicle_id)
            & (TelemetryRecord.event_time == subq.c.max_time),
        )
        .all()
    )
    return {
        r.vehicle_id: {
            "lat": r.lat, "lon": r.lon,
            "speed": r.speed, "heading": r.heading,
            "event_time": r.event_time,
        }
        for r in rows
    }


def _get_acked(redis_client: Redis) -> set[str]:
    try:
        return set(redis_client.smembers(ACK_KEY) or [])
    except Exception:
        return set()


def _status_from_delay(delay_sec: int, has_pos: bool) -> str:
    if not has_pos:
        return "not_seen"
    if delay_sec > 60:
        return "late"      
    if delay_sec < -30:
        return "early"     
    return "on_time"


def _stops_of_route(db: Session, route_id: str) -> list[dict]:
    rows = (
        db.query(Schedule)
        .filter(
            Schedule.route_id == route_id,
            Schedule.lat.isnot(None),
            Schedule.lon.isnot(None),
        )
        .order_by(Schedule.scheduled_arrival)
        .all()
    )
    seen = set()
    out = []
    for r in rows:
        if r.stop_id in seen:
            continue
        seen.add(r.stop_id)
        out.append({
            "id": r.stop_id,
            "name": r.stop_name or r.stop_id,
            "pos": [r.lat, r.lon],
        })
    return out

@router.get("/routes")
def list_routes(db: Session = Depends(get_db)) -> list[dict]:
    route_ids = [
        row[0] for row in
        db.query(Schedule.route_id).distinct().order_by(Schedule.route_id).all()
    ]

    result = []
    for route_id in route_ids:
        stops = _stops_of_route(db, route_id)
        if len(stops) < 2:
            continue
        result.append({
            "id": route_id,
            "num": route_id.replace("route-", "").upper(),
            "name": f"Маршрут {route_id}",
            "type": "rapid",
            "closed": False,
            "stops": [{"name": s["name"], "pos": s["pos"]} for s in stops],
        })
    return result


@router.get("/snapshot")
def snapshot(
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
) -> dict:
    now_ms = int(time.time() * 1000)

    predictions = _get_cached_predictions(redis_client)
    all_vehicle_ids = [
        p["vehicle_id"]
        for recs in predictions.values()
        for p in recs
        if p.get("vehicle_id")
    ]
    positions = _get_last_positions(db, all_vehicle_ids)

    vehicles = []
    for route_id, recs in predictions.items():
        for rec in recs:
            vid = rec.get("vehicle_id")
            if not vid:
                continue
            pos_info = positions.get(vid)
            has_pos = pos_info is not None

            delay_sec = int(rec.get("delay_seconds") or 0)
            prob = float(rec.get("probability") or 0)
            pos = [pos_info["lat"], pos_info["lon"]] if has_pos else [55.752, 37.62]

            vehicles.append({
                "id": vid,
                "routeId": route_id,
                "direction": 0,
                "destination": "по маршруту",
                "pos": pos,
                "segmentIndex": 0,
                "aheadSegmentIndex": 1,
                "pDelay": prob / 100.0,
                "deviationMin": round(delay_sec / 60.0, 1),
                "status": _status_from_delay(delay_sec, has_pos),
                "history": [prob / 100.0] * 5,
            })

    segments = []
    for route_id, recs in predictions.items():
        stops = _stops_of_route(db, route_id)
        if len(stops) < 2:
            continue
        avg_prob = sum(float(r.get("probability") or 0) for r in recs) / max(len(recs), 1)
        risk = avg_prob / 100.0
        for i in range(len(stops) - 1):
            segments.append({
                "id": f"{route_id}:{i}",
                "routeId": route_id,
                "index": i,
                "from": stops[i]["name"],
                "to": stops[i + 1]["name"],
                "coords": [stops[i]["pos"], stops[i + 1]["pos"]],
                "risk": round(risk, 3),
                "cause": "jam" if risk > 0.5 else "light",
            })

    acked = _get_acked(redis_client)
    incidents = []
    for route_id, recs in predictions.items():
        for rec in recs:
            prob = float(rec.get("probability") or 0)
            if prob < 50:
                continue
            vid = rec.get("vehicle_id")
            inc_id = f"inc-{vid}"
            if inc_id in acked:
                continue
            delay_sec = int(rec.get("delay_seconds") or 0)
            incidents.append({
                "id": inc_id,
                "vehicleId": vid,
                "routeId": route_id,
                "direction": 0,
                "destination": "по маршруту",
                "pDelay": prob / 100.0,
                "predictedDelayMin": max(1, round(delay_sec / 60.0)),
                "etaMin": 12,
                "cause": "jam",
                "segmentId": f"{route_id}:0",
                "fromStop": "—",
                "toStop": "—",
                "recommendations": [
                    {
                        "id": f"rec-{vid}-1",
                        "text": rec.get("recommendation") or "Выпустить резервное ТС",
                        "effect": 0.6,
                    },
                ],
            })

    return {
        "ts": now_ms,
        "vehicles": vehicles,
        "segments": segments,
        "incidents": incidents,
    }


@router.post("/incidents/{incident_id}/ack")
def ack_incident(incident_id: str, redis_client: Redis = Depends(get_redis)) -> dict:
    try:
        redis_client.sadd(ACK_KEY, incident_id)
        redis_client.expire(ACK_KEY, ACK_TTL_SECONDS)
    except Exception:
        logger.exception("ack failed")
    return {"status": "ok", "acked": incident_id}


@router.delete("/incidents/{incident_id}/ack")
def unack_incident(incident_id: str, redis_client: Redis = Depends(get_redis)) -> dict:
    try:
        redis_client.srem(ACK_KEY, incident_id)
    except Exception:
        logger.exception("unack failed")
    return {"status": "ok", "unacked": incident_id}
@router.get("/whatif/{vehicle_id}")
def whatif(
    vehicle_id: str,
    extra_vehicles: int = 1,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
) -> dict:
    current = None
    current_route = None
    for key in redis_client.scan_iter(match="route:*:vehicles", count=100):
        for raw in redis_client.zrange(key, 0, -1):
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("vehicle_id") == vehicle_id:
                current = rec
                current_route = rec.get("route_id")
                break
        if current:
            break

    if current is None:
        return {
            "vehicle_id": vehicle_id,
            "found": False,
            "message": "Прогноз для этого борта не найден в кэше",
        }

    old_delay = int(current.get("delay_seconds") or 0)
    old_prob = float(current.get("probability") or 0)
    old_risk = current.get("risk_level", "low")

    reduction = min(0.8, 0.2 * max(0, extra_vehicles))
    new_delay = int(round(old_delay * (1 - reduction)))
    new_prob = round(old_prob * (1 - reduction), 1)

    if new_delay > 120:
        new_risk = "high"
    elif new_delay > 60:
        new_risk = "medium"
    else:
        new_risk = "low"

    return {
        "vehicle_id": vehicle_id,
        "route_id": current_route,
        "found": True,
        "extra_vehicles": extra_vehicles,
        "reduction_pct": int(reduction * 100),
        "before": {
            "delay_seconds": old_delay,
            "probability": old_prob,
            "risk_level": old_risk,
        },
        "after": {
            "delay_seconds": new_delay,
            "probability": new_prob,
            "risk_level": new_risk,
        },
    }