"""
Генерирует расписание для route-101 на основе реальных координат эмулятора.
Автоматически находит контейнер Postgres и читает текущую позицию эмулятора.
"""
import math
import subprocess
import requests
from datetime import datetime, timedelta, timezone

# ==== 1. Автоматически находим контейнер Postgres ====
ps_result = subprocess.run(
    ["docker", "ps", "--filter", "ancestor=postgres:16", "--format", "{{.Names}}"],
    capture_output=True, text=True,
)
pg_container = ps_result.stdout.strip().split("\n")[0]
print(f"Postgres контейнер: {pg_container}")

if not pg_container:
    print("ОШИБКА: не нашли контейнер Postgres. Проверь, что docker compose запущен.")
    exit(1)

# ==== 2. Читаем координаты центра эмулятора из telemetry ====
sql = (
    "SELECT AVG(lat), AVG(lon) FROM telemetry "
    "WHERE event_time > NOW() - INTERVAL '2 minutes';"
)
result = subprocess.run(
    ["docker", "exec", pg_container, "psql", "-U", "postgres",
     "-d", "postgres", "-t", "-A", "-c", sql],
    capture_output=True, text=True,
)

line = result.stdout.strip()
print(f"SQL результат: {line!r}")

if not line or line == "|":
    print("ОШИБКА: нет данных в telemetry за последние 2 минуты.")
    print("stdout:", result.stdout)
    print("stderr:", result.stderr)
    exit(1)

parts = line.split("|")
if len(parts) < 2 or not parts[0]:
    print(f"ОШИБКА: не могу распарсить координаты: {line!r}")
    exit(1)

CENTER_LAT = float(parts[0])
CENTER_LON = float(parts[1])
print(f"Центр эмулятора: {CENTER_LAT}, {CENTER_LON}")

# ==== 3. Строим эллипс остановок вокруг центра ====
LAT_RADIUS = 0.005   # ~550 метров
LON_RADIUS = 0.004   # ~280 метров

STOPS = []
for i in range(10):
    angle = 2 * math.pi * i / 10
    STOPS.append({
        "stop_id": f"route-101-stop-{i+1}",
        "stop_name": f"Остановка {i+1}",
        "lat": CENTER_LAT + LAT_RADIUS * math.sin(angle),
        "lon": CENTER_LON + LON_RADIUS * math.cos(angle),
    })

# ==== 4. Отправляем расписание через API ====
API = "http://localhost:8001/schedules/"
TOKEN = "Xk9mP2qL7wN4vR8tY6bZ3cF1dH5jM8nQ"
headers = {"Authorization": f"Bearer {TOKEN}"}

now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
payload = []
for run_offset in range(0, 360, 3):   # рейсы каждые 3 минуты на 6 часов
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