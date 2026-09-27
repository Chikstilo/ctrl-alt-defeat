import json

from fastapi import APIRouter, Depends
from redis import Redis

from cache import get_redis

router = APIRouter(prefix="/dispatch", tags=["Dispatch"])


@router.get("/recommendations")
def get_recommendations(redis_client: Redis = Depends(get_redis)) -> dict:
    alerts = []

    for key in redis_client.scan_iter(match="route:*:vehicles", count=100):
        for raw in redis_client.zrange(key, 0, -1):
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if item.get("risk_level") not in ("medium", "high"):
                continue

            alerts.append({
                "route_id": item["route_id"],
                "vehicle_id": item["vehicle_id"],
                "delay_seconds": item["delay_seconds"],
                "probability": item.get("probability"),
                "risk_level": item["risk_level"],
                "arrival_time": item.get("arrival_time"),       # вместо predicted_for
                "scheduled_arrival": item.get("scheduled_arrival"),
                "recommendation": item.get("recommendation"),
            })

    priority = {"high": 0, "medium": 1}
    alerts.sort(
        key=lambda item: (
            priority.get(item["risk_level"], 2),
            -item["delay_seconds"],
        )
    )

    return {"count": len(alerts), "alerts": alerts}
