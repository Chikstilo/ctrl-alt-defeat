# Тестовая заливка: расписание + HTTP-телеметрия. Для отладки без эмулятора
# Запуск python seed_data.py
import random
import sys
from datetime import datetime, timedelta
import requests

API_BASE = "http://127.0.0.1:8001"
ROUTES = ["route-100", "route-101", "route-102"]
CENTER_LAT, CENTER_LON = 55.7558, 37.6173


def schedule(route_id: str, n: int = 10):
    now = datetime.utcnow().replace(second=0, microsecond=0)
    return [
        {
            "route_id": route_id,
            "stop_id": f"{route_id}-stop-{i+1}",
            "stop_name": f"Остановка {i+1}",
            "scheduled_arrival": (now + timedelta(minutes=i * 10)).isoformat(),
        }
        for i in range(n)
    ]


def main():
    print(f"Заливка в {API_BASE}")
    for r in ROUTES:
        try:
            resp = requests.post(f"{API_BASE}/schedules/", json=schedule(r), timeout=10)
            print(f"{r}: {resp.status_code}")
        except requests.exceptions.ConnectionError:
            print(f"Нет соединения с {API_BASE}")
            sys.exit(1)

    total = 0
    for route_id in ROUTES:
        for i in range(1, 5):
            vehicle_id = f"bus-{route_id.split('-')[-1]}-{i}"
            payload = {
                "vehicle_id": vehicle_id,
                "route_id": route_id,
                "lat": CENTER_LAT + random.uniform(-0.05, 0.05),
                "lon": CENTER_LON + random.uniform(-0.05, 0.05),
                "speed": random.uniform(0, 60),
                "stop_id": f"{route_id}-stop-{random.randint(1, 10)}",
                "dwell_time_seconds": random.uniform(0, 240),
                "event_time": datetime.utcnow().isoformat(),
            }
            try:
                resp = requests.post(f"{API_BASE}/telemetry/", json=payload, timeout=5)
                if resp.status_code == 201:
                    total += 1
            except Exception as e:
                print(f"   ❌ {e}")
    print(f"\nОтправлено: {total}")


if __name__ == "__main__":
    main()