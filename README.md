# Предиктор задержек наземного транспорта — ML-часть

Инструкция по запуску и проверке ML-части решения. Ниже описано: что делает
каждый файл, как всё запустить с нуля, что означают выводимые метрики и как
убедиться, что в решении нет утечки данных (data leakage).

---

## 1. Состав решения

| Файл | Назначение |
| --- | --- |
| `main.py` | Загрузка данных, построение признаков (`FeatureEngine`) — ядро решения |
| `train_and_predict.py` | Обучение CatBoost, локальная оценка качества, генерация `submission.csv` |
| `leakage_check.py` | Диагностика утечки/переобучения на бортах, не участвовавших в обучении |

---

## 2. Задача в двух словах

Прогноз строится в момент времени `T` для остановки, чьё **плановое** время
прибытия попадает в окно `(T+10 мин, T+15 мин]`. Модель предсказывает
**фактическую задержку** (секунды, знак важен: `+` — опоздание, `−` —
опережение). Метрика — MAE (средняя абсолютная ошибка), меньше — лучше.

**Anti-leakage правило, соблюдаемое во всём пайплайне:** при построении
признаков для точки `(tr_id, T)` используются только данные с
`event_time ≤ T` (телеметрия) и `time_fact_begin ≤ T` (расписание — только
уже свершившиеся прибытия, а не плановые). Это ключевое ограничение задачи,
и его соблюдение — то, что стоит проверить в первую очередь.

---

## 3. Требования к окружению

```bash
pip install catboost pyproj shapely scipy pandas numpy scikit-learn
```

Проверено на Python 3.12+. `pyproj`/`shapely` используются для точных
геодезических расчётов расстояний (WGS84); если их нет, код автоматически
переключается на ручной расчёт (haversine) — итог тот же, установка
необязательна, но желательна.

---

## 4. Структура данных

Все скрипты ожидают, что данные лежат в одной папке (ниже — `DATA_DIR`) со
следующей структурой (как в исходной раздаче):

```text
DATA_DIR/
├── train/
│   ├── traffic.csv
│   └── schedule.csv
├── test/
│   ├── traffic.csv
│   └── schedule.csv
├── labels/
│   ├── labels_train.csv
│   └── labels_test.csv
└── validate/
    ├── traffic.csv
    ├── schedule_plan.csv
    └── points.csv
```

Путь к этой папке задаётся переменной `DATA_DIR` в начале `train_and_predict.py`
— это единственное, что нужно поменять перед запуском.

---

## 5. Порядок запуска

### Шаг 1 — обучение модели

```bash
python train_and_predict.py
```

Скрипт:

1. Строит признаки по `train` и `test` отдельно (телеметрия + расписание +
   геометрия маршрута + peer-признаки от соседних бортов).
2. Обучает CatBoost на `train`, с ранней остановкой по внутреннему
   валидационному сплиту (разбиение **по бортам**, `GroupShuffleSplit`, а не
   по случайным строкам — иначе один и тот же борт мог бы одновременно
   попасть и в обучение, и в валидацию).
3. Оценивает модель на `test` (полностью независимая от обучения выборка) —
   печатает MAE против двух бейзлайнов (`predict 0` и `predict cur_dev_s`).
4. Прогоняет ablation-тест (влияние ID борта на переобучение) и
   object importance (какие строки трейна мешают, а не помогают модели).
5. Дообучает финальную модель на `train+test`, строит прогноз для `validate`
   и сохраняет `submission.csv`.
6. Сохраняет артефакты для backend: `model.cbm` (веса модели) и
   `model_contract.json` (точный порядок и состав признаков — обязателен для
   корректного инференса).

**На что смотреть в выводе:**

```text
MAE (predict 0)          : ...  <- наивный прогноз "задержки нет"
MAE (baseline cur_dev_s) : ...  <- прогноз "будет как сейчас"
MAE (CatBoost, test)     :   <- наша модель
улучшение над бейзлайном : ...%
```

Модель имеет смысл только если она заметно обходит оба бейзлайна. Если
`MAE (CatBoost, test)` близко к `MAE (predict 0)` — модель не научилась
ничему полезному; если MAE подозрительно близко к 0 — см. раздел 6.

### Шаг 2 — проверка на утечку/переобучение

```bash
python leakage_check.py
```

Делит борта `train` на две непересекающиеся группы (2/3 / 1/3), обучает
модель **только** на одной группе, проверяет на бортах, которых модель
никогда не видела. Три возможных исхода:

| Результат | Интерпретация |
| --- | --- |
| MAE ≈ 0 (аномально идеально) | Подозрение на утечку — ответ каким-то образом протекает в признаки независимо от конкретного борта |
| MAE близко к бейзлайну (`predict 0`) | Модель не обобщается на новые борта — переобучение |
| MAE в разумных пределах, сопоставимо с MAE из шага 1 | Ожидаемое, здоровое поведение |

## 6. Как проверить, что утечки нет (чек-лист для эксперта)

1. **Временная утечка.** Убедиться, что признаки истории борта строятся по
   `time_fact_begin ≤ T`, а не по `time_begin ≤ T` (в `main.py`, метод
   `FeatureEngine._own_schedule_upto`). Плановое время прибытия может уже
   наступить, а факт — ещё нет, если борт опаздывает; фильтрация по плану
   в такой ситуации даёт прямую утечку ответа.
2. **Утечка через идентификатор.** Целевая остановка (`target_stop_id`)
   не должна повторяться между `train` и `test` с одинаковым значением
   задержки (проверяется отдельно, см. раздел 7).
3. **Результат `leakage_check.py`** — MAE на незнакомых бортах не должен
   быть аномально низким (см. таблицу выше).
4. **Геометрические признаки** (`route_dist_to_target_m`,
   `n_stops_to_target`) используют только *позиции* остановок маршрута
   (публичная инфраструктура), а не факты о задержках — не являются
   источником утечки, даже если технически смотрят на остановки, ещё не
   пройденные бортом.

---

## 7. Известные ограничения и то, что стоит перепроверить эксперту

- Признак `target_stop_id_cat` почти всегда уникален на каждую строку
  (id конкретного планового прибытия) — не несёт риска утечки между
  train/test, но и почти не несёт пользы; в текущей версии модели исключён
  из числа категориальных признаков в пользу `stop_geo_bucket` (обобщается
  по географии, а не по уникальному id).
- Оценка на `test` строится на ограниченном числе бортов (пересекается с
  флотом `train`, т.к. данные — один день) — качество на действительно
  новом дне/флоте может отличаться; для этого и нужен `leakage_check.py`
  на новых бортах внутри одного дня как приближённая оценка обобщающей
  способности.
  
# BackEnd часть
MoscTrans Backend

Backend предиктора задержек городского транспорта для хакатона МосТранса. Система принимает поток телематики NDTP, обогащает её данными расписания, прогнозирует задержки на горизонте 10-15 минут с помощью CatBoost, отдаёт прогнозы через REST API и визуализирует на дашборде диспетчера.

АРХИТЕКТУРА

Эмулятор NDTP -> TCP:9201 -> ndtp_server -> Redis Stream (ndtp:stream) -> worker (Consumer Group) -> ml_service (CatBoost) / Redis ZSET (кэш) / PostgreSQL (история) -> FastAPI /api/v1/* -> React-дашборд (localhost:5173).

Модули:

postgres — хранение телеметрии, прогнозов, расписания, привязок. Порт на хосте 5433.
redis — очередь (Stream), кэш прогнозов, heartbeat. Порт на хосте 6380.
api — FastAPI, REST API для дашборда. Порт на хосте 8001.
ndtp_server — TCP-сервер, принимает бинарные NDTP-пакеты. Порт на хосте 9201.
worker — читает Redis Stream, строит фичи, вызывает ML.
ml_service — CatBoost-модель для прогноза задержек. Порт на хосте 8002.

Требование ТЗ о разделении на 3 модуля выполнено: Backend (api + worker + ndtp_server), ML-ядро (ml_service), BI-дашборд (frontend).

СТРУКТУРА ПРОЕКТА

Test final/
  docker-compose.yml
  Dockerfile
  requirements.txt
  .env
  .env.example
  alembic.ini
  main.py
  worker.py
  ndtp_server.py
  ndtp_parser.py
  feature_builder.py
  auth.py
  config.py
  database.py
  cache.py
  models.py
  schemas.py
  seed_schedule.py
  endpoints/
  migrations/
  ml_service/
  tests/
  frontend/

БЫСТРЫЙ СТАРТ

Требования: Docker Desktop 20+, Python 3.11+, Node.js 20+, 8 ГБ RAM.

Шаг 1. Перейти в папку проекта.

cd "C:\Users\Стас\Desktop\Test final"

Шаг 2. Настроить .env.

Файл .env должен содержать следующие строки:

DB_USER=postgres
DB_PASSWORD=Xk9mP2qL7wN4vR8tY6bZ3cF1
DB_NAME=postgres
DB_HOST=localhost
DB_PORT=5433
REDIS_HOST=localhost
REDIS_PORT=6380
REDIS_DB=0
ML_SERVICE_URL=http://localhost:8002
NDTP_TCP_PORT=9201
API_TOKEN=Xk9mP2qL7wN4vR8tY6bZ3cF1dH5jM8nQ
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
DEBUG_LOG_FEATURES=0
DEBUG_SAVE_FEATURES=0
DEBUG_LOG_MODEL=0

Шаг 3. Запустить 6 сервисов.

docker compose up -d --build

Start-Sleep -Seconds 30

docker ps

Ожидаемо: 6 контейнеров Up, у 4 из них healthy.

Шаг 4. Запустить эмулятор NDTP.

docker run -d --name ndtp-emulator --restart unless-stopped -p 18080:18080 ndtp-telemetry-emulator:1.0

Start-Sleep -Seconds 15

Дальше выполнить в PowerShell одной командой:

$cfg = '{"targetHost":"host.docker.internal","targetPort":9201,"units":[{"unitId":1166336,"intervalMs":1000,"autoGenerate":true,"cells":[]}]}'

Invoke-RestMethod -Uri "http://localhost:18080/api/config" -Method Post -Body $cfg -ContentType "application/json"

Шаг 5. Привязать автобус к маршруту.

Выполнить в PowerShell по одной команде:

$headers = @{Authorization="Bearer Xk9mP2qL7wN4vR8tY6bZ3cF1dH5jM8nQ"}

$body = '{"unit_id":1166336,"vehicle_id":"bus-1","route_id":"route-101","active":true}'

Invoke-RestMethod -Uri "http://localhost:8001/vehicles/" -Method Post -Body $body -ContentType "application/json" -Headers $headers

Шаг 6. Сгенерировать расписание.

py seed_schedule.py

docker restart testfinal-worker-1

Start-Sleep -Seconds 40

Шаг 7. Проверить backend.

Invoke-RestMethod -Uri "http://localhost:8001/health"

Invoke-RestMethod -Uri "http://localhost:8001/predictions/route-101"

Invoke-RestMethod -Uri "http://localhost:8001/worker/stats"

Шаг 8. Запустить фронтенд в отдельном окне.

cd frontend

"VITE_API_URL=http://localhost:8001" | Out-File -Encoding utf8 .env

npm install

npm run dev

Открыть в браузере http://localhost:5173

API

Полная документация доступна по адресу http://localhost:8001/docs (Swagger UI).

Health и мониторинг:

GET /health — состояние всех сервисов.
GET /worker/stats — статистика воркера.
GET / — корневой ping.

Транспорт:

POST /vehicles/ — создать или обновить привязку unit_id к route_id. Требует токен.
GET /vehicles/ — список привязок.
GET /vehicles/{unit_id} — одна привязка.
DELETE /vehicles/{unit_id} — деактивировать. Требует токен.

Телеметрия:

POST /telemetry/ — HTTP-приём для отладки. Требует токен.
GET /telemetry/{vehicle_id} — история.

Прогнозы:

GET /predictions/{route_id}/latest — последний прогноз.
GET /predictions/{route_id} — все ТС на маршруте.
GET /predictions/{route_id}/history — история из БД.

Расписание:

POST /schedules/ — загрузить расписание. Требует токен.
GET /schedules/{route_id} — расписание маршрута.
DELETE /schedules/{route_id} — удалить. Требует токен.

Диспетчер:

GET /dispatch/recommendations — алерты с рекомендациями.
GET /routes — маршруты с агрегацией.

Дашборд (v1):

GET /api/v1/routes — маршруты для карты.
GET /api/v1/snapshot — снимок для дашборда, обновляется каждые 2 секунды.
POST /api/v1/incidents/{id}/ack — взять инцидент в работу.
DELETE /api/v1/incidents/{id}/ack — вернуть инцидент в очередь.
GET /api/v1/whatif/{vehicle_id}/options — What-if, список всех мер.
GET /api/v1/whatif/{vehicle_id} — What-if, расчёт конкретной меры.

Пример What-if:

GET /api/v1/whatif/bus-1?measure=extra_vehicle&count=2

Ответ в формате JSON:

{
  "vehicle_id": "bus-1",
  "measure": "extra_vehicle",
  "count": 2,
  "reduction_pct": 40,
  "before": {"delay_seconds": 93, "probability": 60, "risk_level": "medium"},
  "after":  {"delay_seconds": 56, "probability": 36, "risk_level": "low"}
}

Доступные меры: extra_vehicle, dwell_reduce, interval_adjust, signal_priority, reroute.

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ

DB_USER — пользователь PostgreSQL. По умолчанию postgres.
DB_PASSWORD — пароль PostgreSQL.
DB_HOST — хост БД. Для Docker = postgres, для хоста = localhost.
DB_PORT — порт БД. Для Docker = 5432, для хоста = 5433.
DB_NAME — имя БД. По умолчанию postgres.
REDIS_HOST — хост Redis. Для Docker = redis, для хоста = localhost.
REDIS_PORT — порт Redis. Для Docker = 6379, для хоста = 6380.
ML_SERVICE_URL — URL ML-сервиса. По умолчанию http://localhost:8002.
NDTP_TCP_PORT — порт TCP-сервера. По умолчанию 9201.
API_TOKEN — Bearer-токен для write-эндпоинтов.
ALLOWED_ORIGINS — разрешённые origins для CORS.
DEBUG_LOG_FEATURES — логировать фичи воркера. По умолчанию 0.
DEBUG_SAVE_FEATURES — сохранять фичи в Redis. По умолчанию 0.
DEBUG_LOG_MODEL — логировать вход и выход ML. По умолчанию 0.

КАК ЭТО РАБОТАЕТ

Эмулятор отправляет бинарные NDTP-пакеты на TCP:9201, один пакет в секунду. Скрипт ndtp_server.py парсит пакет через ndtp_parser.py: проверяет NPL и NPH-заголовки, CRC, извлекает навигацию (lat, lon, speed, heading), обогащает данными из vehicle_routes (unit_id -> bus-1), пишет в telemetry (PostgreSQL) и ndtp:stream (Redis). Скрипт worker.py читает ndtp:stream через Consumer Groups: обновляет in-memory буфер телеметрии в feature_builder, детектирует прохождение остановок и пишет в stop_crossings, строит 45 фич через feature_builder.py, вызывает ML-сервис через POST /predict, сохраняет прогноз в predictions и кэш в Redis ZSET, подтверждает сообщение через XACK. Сервис ml_service — CatBoost-модель по 45 фичам возвращает delay_seconds. FastAPI отдаёт прогнозы через /api/v1/* (для дашборда) и /predictions/* (общий API). React-дашборд опрашивает /api/v1/snapshot каждые 2 секунды.

Heartbeat.
ndtp_server пишет в Redis ключ ndtp:heartbeat с TTL 10 секунд каждые 5 секунд. API читает его в /health и честно показывает ndtp_tcp true или false.

Привязка unit_id к route_id.
Таблица vehicle_routes в PostgreSQL. Эмулятор шлёт только unitId. ndtp_server ищет по нему vehicle_id и route_id. Если привязки нет, сохраняет под числовым ID.

Детекция остановок.
worker.py ищет остановки в радиусе STOP_PROXIMITY_THRESHOLD_M от текущей позиции, записывает в stop_crossings с ON CONFLICT DO NOTHING (защита от дублей через UNIQUE constraint). В feature_builder.py фичи own_delay_* считаются по последним 50 crossings.

45 фич для CatBoost.

Тривиальные (7): tr_id_cat, target_stop_id_cat, stop_geo_bucket, time_to_target_s, cur_dev_s, hour, dow.

Оконные (15): speed_mean_5m, speed_mean_10m, speed_mean_15m, speed_std_5m, speed_std_10m, speed_std_15m, stuck_frac_5m, stuck_frac_10m, stuck_frac_15m, heading_change_5m, heading_change_10m, heading_change_15m, n_points_5m, n_points_10m, n_points_15m.

Гео (7): dist_to_target_m, route_dist_to_target_m, n_stops_to_target, route_over_straight_ratio, implied_speed_kmh, implied_speed_kmh_straight, speed_deficit_10m.

История (6): n_stops_passed, own_delay_last, own_delay_mean_last3, own_delay_trend, own_delay_mean_all, own_delay_std_all.

Peer (5): peer_speed_mean_target, peer_stuck_frac_target, peer_n_target, peer_speed_mean_here, peer_n_here.

Прочее (5): last_speed, last_heading, time_since_fix_s, cur_dev_trend, cur_dev_avg_last3.

ML-МОДЕЛЬ

CatBoost Regressor обучен ML-инженером на 100 000 примерах реального трафика Москвы. MAE на offline-тесте 55.3 секунды. Порядок фич строго по model_contract.json, пересобирается в ml_service/model.py. Пороги risk_level: больше 120 — high, больше 60 — medium, иначе low. Fallback: если ML-сервис недоступен, воркер использует эвристику по скорости и расписанию.

Метрики из /worker/stats:

fallback_used — сколько прогнозов ушло в эвристику. Должно быть 0 при работающей модели.
crossings — количество зафиксированных прохождений остановок.
processed — всего обработано сообщений.

ПРОИЗВОДИТЕЛЬНОСТЬ

Latency ML-инференса: около 15 мс (по логам ml_service).
Пропускная способность: 1 пакет в секунду на борт.
Холодный старт Docker: около 30 секунд.
Деградация при обрыве NDTP: fallback на эвристику, проверено через docker stop ndtp-emulator.
Восстановление после реконнекта: автоматическое через heartbeat и ensure_group.
Размер БД: около 50 МБ на 1 борт за 30 минут.

Надёжность:

Redis Stream Consumer Groups с XACK — нет потери сообщений при падении воркера.
XAUTOCLAIM — воркер забирает зависшие сообщения от упавших consumers.
ProcessedStreamMessage — таблица идемпотентности, защита от повторной обработки.
Heartbeat — /health честно показывает состояние TCP-сервера.
Fallback ML — воркер продолжает работу при недоступности ML-сервиса.

ТЕСТИРОВАНИЕ

Запуск тестов:

cd "C:\Users\Стас\Desktop\Test final"

py -m pytest tests/ -v

Ожидаемо: 12–18 тестов, все PASSED.

Что покрыто:

test_parser.py — NDTP-парсер (корректные realtime и handshake-кадры, битый CRC, битая сигнатура, слишком короткие кадры, отрицательные координаты, граничные значения).

test_api.py — REST API (/health, /vehicles/, /predictions/, авторизация 401 без токена, 404 для несуществующих ресурсов).

Ручное тестирование:

Invoke-RestMethod -Uri "http://localhost:8001/health"

Invoke-RestMethod -Uri "http://localhost:8001/api/v1/whatif/bus-1/options"

Invoke-RestMethod -Uri "http://localhost:8001/api/v1/whatif/bus-1?measure=extra_vehicle&count=2"

Проверка деградации:

docker stop ndtp-emulator

Start-Sleep -Seconds 12

Invoke-RestMethod -Uri "http://localhost:8001/health"

Ожидаемо: ndtp_tcp false, всё остальное true.

docker start ndtp-emulator

Start-Sleep -Seconds 15

Invoke-RestMethod -Uri "http://localhost:8001/health"

Ожидаемо: ndtp_tcp true.

РЕШЕНИЕ ПРОБЛЕМ

Проблема: port is already allocated.
Решение:

cd "C:\Users\Стас\Desktop\Test final"

docker compose down

docker container prune -f

docker compose up -d

Проблема: could not translate host name postgres.
Решение:

docker compose down

docker network prune -f

docker compose up -d

Проблема: constraint uq_stop_crossing_vehicle_stop_planned does not exist.
Решение:

docker exec testfinal-postgres-1 psql -U postgres -d postgres -c "ALTER TABLE stop_crossings ADD CONSTRAINT uq_stop_crossing_vehicle_stop_planned UNIQUE (vehicle_id, stop_id, planned_time);"

docker restart testfinal-worker-1

Проблема: fallback_used растёт, crossings равно 0.
Решение:

docker exec testfinal-postgres-1 psql -U postgres -d postgres -c "DELETE FROM schedules WHERE route_id='route-101';"

py seed_schedule.py

docker restart testfinal-worker-1

Проблема: прогнозы всегда low, probability 20.
Это нормально для синтетического эмулятора (задержки меньше 60 секунд). Для демонстрации medium и high сдвиньте расписание на 8–15 минут назад:

docker exec testfinal-postgres-1 psql -U postgres -d postgres -c "UPDATE schedules SET scheduled_arrival = scheduled_arrival - INTERVAL '8 minutes' WHERE route_id = 'route-101';"

docker restart testfinal-worker-1

Проблема: CORS error в браузере.
Решение: добавить URL фронта в ALLOWED_ORIGINS в .env:

ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000

Пересобрать API:

docker compose up -d --build api

Проблема: фронт не подключается к API.
Проверить frontend/.env:

VITE_API_URL=http://localhost:8001

Перезапустить npm run dev (Vite читает .env только при старте).

СТЕК ТЕХНОЛОГИЙ

Web framework — FastAPI 0.115+.
ORM — SQLAlchemy 2.0.
Миграции — Alembic.
БД — PostgreSQL 16.
Очередь и кэш — Redis 7 (Streams, ZSET).
ML — CatBoost, pandas, numpy.
Авторизация — Bearer Token.
Контейнеризация — Docker, Docker Compose.
Python — 3.12.
Фронт — React 18, TypeScript, Vite.

ССЫЛКИ

Swagger API: http://localhost:8001/docs
Дашборд: http://localhost:5173
OpenAPI JSON: http://localhost:8001/openapi.json

Проект разработан командой для хакатона Московского транспорта, сентябрь 2026.