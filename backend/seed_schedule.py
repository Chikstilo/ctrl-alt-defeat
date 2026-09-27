"""
Автоматически подстраивается под текущий маршрут эмулятора.
Читает координаты из последних пакетов telemetry и строит расписание внутри.
"""
import math
import subprocess
import requests
from datetime import datetime, timedelta, timezone

# Получаем реальные координаты из БД
result = subprocess.run(
    ["docker", "exec", "hackathon-postgres-1", "psql", "-U", "postgres",
     "-d", "postgres", "-t", "-c",
     "SELECT AVG(lat), AVG(lon) FROM telemetry "
     "WHERE event_time > NOW() - INTERVAL '2 minutes';"],
    capture_output=True, text=True
)
line = result.stdout.strip().split("|")
CENTER_LAT = float(line[0])
CENTER_LON = float(line[1])
print(f"Центр эмулятора: {CENTER_LAT}, {CENTER_LON}")

# Оси эллипса — 500 и 300 метров
LAT_RADIUS = 0.005
LON_RADIUS = 0.004

STOPS = []
for i in range(10):
    angle = 2 * math.pi * i / 10
    STOPS.append({
        "stop_id": f"route-101-stop-{i+1}",
        "stop_name": f"Остановка {i+1}",
        "lat": CENTER_LAT + LAT_RADIUS * math.sin(angle),
        "lon": CENTER_LON + LON_RADIUS * math.cos(angle),
    })

# Регистрация
API = "http://localhost:8001/schedules/"
TOKEN = "Xk9mP2qL7wN4vR8tY6bZ3cF1dH5jM8nQ"
headers = {"Authorization": f"Bearer {TOKEN}"}

now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
payload = []
for run_offset in range(0, 360, 3):
    run_start = now + timedelta(minutes=run_offset)
    for i, stop in enumerate(STOPS):
        payload.append({
            "route_id": "route-101",
            "stop_id": stop["stop_id"],
            "stop_name": stop["stop_name"],
            "scheduled_arrival": (run_start + timedelta(minutes=i)).isoformat(),
            "lat": stop["lat"],
            "lon": stop["lon"],
        })

print(f"Отправляю {len(payload)} записей...")
resp = requests.post(API, json=payload, headers=headers, timeout=30)
print(f"Статус: {resp.status_code}")
print(f"Ответ: {resp.json()}")