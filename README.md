# Предиктор задержек наземного транспорта

Инструкция по запуску и проверке решения целиком: **Часть I** — ML-часть
(обучение модели, признаки, проверка на утечку данных), **Часть II** —
backend и инфраструктура (как поднять весь стек и прогнать прогноз от
эмулятора NDTP до дашборда).

---

# Часть I. ML-часть

Ниже описано: что делает каждый файл, как всё запустить с нуля, что означают
выводимые метрики и как убедиться, что в решении нет утечки данных (data
leakage).

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
MAE (CatBoost, test)     : ...  <- наша модель
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

---

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
- Признак `tr_id_cat` (идентификатор борта) по ablation-тесту показал
  ухудшение метрики — исключён из финального набора признаков.
- Оценка на `test` строится на ограниченном числе бортов (пересекается с
  флотом `train`, т.к. данные — один день) — качество на действительно
  новом дне/флоте может отличаться; для этого и нужен `leakage_check.py`
  на новых бортах внутри одного дня как приближённая оценка обобщающей
  способности.

---

# Часть II. Backend и инфраструктура (MoscTrans Backend)

Бэкенд-предиктор задержек городского транспорта для хакатона МосТранса.
Система принимает поток телематики NDTP, дополняет его данными расписания,
прогнозирует задержки на горизонте 10–15 минут с помощью CatBoost, передаёт
прогнозы через REST API и визуализирует их на дашборде диспетчера.

## 9. Архитектура

```text
Эмулятор NDTP → TCP:9201 → ndtp_server → Redis Stream (ndtp:stream)
    → worker (consumer group) → ml_service (CatBoost) / Redis ZSET (кэш)
    / PostgreSQL (история) → FastAPI /api/v1/* → React-дашборд (localhost:5173)
```

Модули:

| Модуль | Роль | Порт на хосте |
| --- | --- | --- |
| `postgres` | Хранение телеметрии, прогнозов, расписания, привязок | 5433 |
| `redis` | Очередь (Stream), кэш прогнозов, heartbeat | 6380 |
| `api` | FastAPI, REST API для дашборда | 8001 |
| `ndtp_server` | TCP-сервер, принимающий бинарные NDTP-пакеты | 9201 |
| `worker` | Читает Redis Stream, строит признаки, вызывает ML | — |
| `ml_service` | Модель CatBoost для прогнозирования задержек | 8002 |

Требование технического задания о разделении на 3 модуля выполнено:
backend (`api` + `worker` + `ndtp_server`), ML-ядро (`ml_service`),
BI-дашборд (фронтенд).

## 10. Структура проекта

```text
Test final/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env
├── .env.example
├── alembic.ini
├── main.py
├── worker.py
├── ndtp_server.py
├── ndtp_parser.py
├── feature_builder.py
├── auth.py
├── config.py
├── database.py
├── cache.py
├── models.py
├── schemas.py
├── seed_schedule.py
├── endpoints/
├── migrations/
├── ml_service/
├── tests/
└── frontend/
```

## 11. Быстрый старт

**Требования:** Docker Desktop 20+, Python 3.12+, Node.js 20+, 8 ГБ RAM.

**Шаг 1 — перейти в папку проекта:**

```powershell
cd "ВСТАВЬТЕ ВАШ ПУТЬ"
```

**Шаг 2 — настроить `.env`.** Файл `.env` должен содержать:

```text
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
```

**Шаг 3 — запустить 6 сервисов:**

```powershell
docker compose up -d --build
Start-Sleep -Seconds 30
docker ps
```

Ожидаемо: 6 контейнеров `Up`, у 4 из них `healthy`.

**Шаг 4 — запустить эмулятор NDTP:**

```powershell
docker run -d --name ndtp-emulator --restart unless-stopped `
  -p 18080:18080 ndtp-telemetry-emulator:1.0
Start-Sleep -Seconds 15

$cfg = '{"targetHost":"host.docker.internal","targetPort":9201,"units":[{"unitId":1166336,"intervalMs":1000,"autoGenerate":true,"cells":[]}]}'
Invoke-RestMethod -Uri "http://localhost:18080/api/config" -Method Post -Body $cfg -ContentType "application/json"
```

**Шаг 5 — привязать автобус к маршруту:**

```powershell
$headers = @{ Authorization = "Bearer Xk9mP2qL7wN4vR8tY6bZ3cF1dH5jM8nQ" }
$body = '{"unit_id": 1166336, "vehicle_id": "bus-1", "route_id": "route-101", "active": true}'
Invoke-RestMethod -Uri "http://localhost:8001/vehicles/" -Method Post -Body $body -ContentType "application/json" -Headers $headers
```

**Шаг 6 — сгенерировать расписание:**

```powershell
py seed_schedule.py
docker restart testfinal-worker-1
Start-Sleep -Seconds 40
```

**Шаг 7 — проверить backend:**

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/health"
Invoke-RestMethod -Uri "http://localhost:8001/predictions/route-101"
Invoke-RestMethod -Uri "http://localhost:8001/worker/stats"
```

**Шаг 8 — запустить фронтенд в отдельном окне:**

```powershell
cd frontend
"VITE_API_URL=http://localhost:8001" | Out-File -Encoding utf8 .env
npm install
npm run dev
```

Открыть в браузере: `http://localhost:5173`.

## 12. API

Полная документация — `http://localhost:8001/docs` (Swagger UI).

**Health и мониторинг:**

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/health` | Состояние всех сервисов |
| GET | `/worker/stats` | Статистика воркера |
| GET | `/` | Корневой пинг |

**Транспорт:**

| Метод | Путь | Описание |
| --- | --- | --- |
| POST | `/vehicles/` | Создать/обновить привязку `unit_id` → `route_id`. Нужен токен |
| GET | `/vehicles/` | Список привязок |
| GET | `/vehicles/{unit_id}` | Одна привязка |
| DELETE | `/vehicles/{unit_id}` | Деактивировать. Нужен токен |

**Телеметрия:**

| Метод | Путь | Описание |
| --- | --- | --- |
| POST | `/telemetry/` | HTTP-приём для отладки. Нужен токен |
| GET | `/telemetry/{vehicle_id}` | История |

**Прогнозы:**

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/predictions/{route_id}/latest` | Последний прогноз |
| GET | `/predictions/{route_id}` | Все ТС на маршруте |
| GET | `/predictions/{route_id}/history` | История из БД |

**Расписание:**

| Метод | Путь | Описание |
| --- | --- | --- |
| POST | `/schedules/` | Загрузить расписание. Нужен токен |
| GET | `/schedules/{route_id}` | Расписание маршрута |
| DELETE | `/schedules/{route_id}` | Удалить. Нужен токен |

**Диспетчер:**

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/dispatch/recommendations` | Алерты с рекомендациями |
| GET | `/routes` | Маршруты с агрегацией |

**Дашборд (v1):**

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/api/v1/routes` | Маршруты для карты |
| GET | `/api/v1/snapshot` | Снимок для дашборда, обновляется каждые 2 секунды |
| POST | `/api/v1/incidents/{id}/ack` | Взять инцидент в работу |
| DELETE | `/api/v1/incidents/{id}/ack` | Вернуть инцидент в очередь |
| GET | `/api/v1/whatif/{vehicle_id}/options` | What-if, список всех мер |
| GET | `/api/v1/whatif/{vehicle_id}` | What-if, расчёт конкретной меры |

**Пример What-if:**

```text
GET /api/v1/whatif/bus-1?measure=extra_vehicle&count=2
```

Ответ (структура и поля восстановлены по смыслу — оригинальный JSON в
источнике был повреждён повторяющимися кавычками, поэтому перепроверь
точные названия ключей и значения на своём реально работающем API,
например через `/docs`):

```json
{
  "vehicle_id": "bus-1",
  "measure": "extra_vehicle",
  "count": 2,
  "reduction_pct": 40,
  "before": {
    "delay_seconds": 93,
    "probability": 60,
    "risk_level": "medium"
  },
  "after": {
    "delay_seconds": "...",
    "probability": "...",
    "risk_level": "..."
  }
}
```

Доступные меры: `extra_vehicle`, `dwell_reduce`, `interval_adjust`,
`signal_priority`, `reroute`.

## 13. Переменные окружения

| Переменная | Назначение |
| --- | --- |
| `DB_USER` | Пользователь PostgreSQL. По умолчанию `postgres` |
| `DB_PASSWORD` | Пароль PostgreSQL |
| `DB_HOST` | Хост БД. Для Docker — `postgres`, для хоста — `localhost` |
| `DB_PORT` | Порт БД. Для Docker — `5432`, для хоста — `5433` |
| `DB_NAME` | Имя БД. По умолчанию `postgres` |
| `REDIS_HOST` | Хост Redis. Для Docker — `redis`, для хоста — `localhost` |
| `REDIS_PORT` | Порт Redis. Для Docker — `6379`, для хоста — `6380` |
| `ML_SERVICE_URL` | URL ML-сервиса. По умолчанию `http://localhost:8002` |
| `NDTP_TCP_PORT` | Порт TCP-сервера. По умолчанию `9201` |
| `API_TOKEN` | Bearer-токен для эндпоинтов записи |
| `ALLOWED_ORIGINS` | Разрешённые источники для CORS |
| `DEBUG_LOG_FEATURES` | Логирование признаков воркера. По умолчанию `0` |
| `DEBUG_SAVE_FEATURES` | Сохранение признаков в Redis. По умолчанию `0` |
| `DEBUG_LOG_MODEL` | Логирование входа/выхода ML. По умолчанию `0` |

## 14. Как это работает

Эмулятор отправляет бинарные NDTP-пакеты на `TCP:9201`, по одному пакету в
секунду. `ndtp_server.py` обрабатывает пакет через `ndtp_parser.py`:
проверяет заголовки NPL и NPH, CRC, извлекает навигационные данные (широта,
долгота, скорость, направление), дополняет данными из `vehicle_routes`
(`unit_id → bus-1`), записывает в телеметрию (PostgreSQL) и `ndtp:stream`
(Redis).

`worker.py` читает `ndtp:stream` через consumer group: обновляет буфер
телеметрии в памяти в `feature_builder`, определяет прохождение остановок и
записывает в `stop_crossings`, строит 45 признаков через
`feature_builder.py`, вызывает ML-сервис через `POST /predict`, сохраняет
прогноз в `predictions` и кэширует в Redis ZSET, подтверждает сообщение
через `XACK`.

`ml_service` — модель CatBoost по 45 признакам — возвращает `delay_seconds`.
FastAPI отдаёт прогнозы через `/api/v1/*` (для дашборда) и `/predictions/*`
(общий API). React-дашборд запрашивает `/api/v1/snapshot` каждые 2 секунды.

**Heartbeat.** `ndtp_server` каждые 5 секунд пишет в Redis ключ
`ndtp:heartbeat` с TTL 10 секунд. API читает его в `/health` и честно
показывает `ndtp_tcp: true/false`.

**Привязка `unit_id` к `route_id`.** Таблица `vehicle_routes` в PostgreSQL.
Эмулятор отправляет только `unitId`. `ndtp_server` ищет по нему `vehicle_id`
и `route_id`. Если привязки нет — сохраняет под числовым идентификатором.

**Обнаружение остановок.** `worker.py` ищет остановки в радиусе
`STOP_PROXIMITY_THRESHOLD_M` от текущей позиции, записывает в
`stop_crossings` с `ON CONFLICT DO NOTHING` (защита от дублирования через
`UNIQUE`-ограничение). В `feature_builder.py` признаки `own_delay_*`
считаются по последним 50 пересечениям.

## 15. 45 признаков для CatBoost

| Группа | Признаки |
| --- | --- |
| Тривиальные (7) | `tr_id_cat`, `target_stop_id_cat`, `stop_geo_bucket`, `time_to_target_s`, `cur_dev_s`, `hour`, `dow` |
| Оконные (15) | `speed_mean_5m/10m/15m`, `speed_std_5m/10m/15m`, `stuck_frac_5m/10m/15m`, `heading_change_5m/10m/15m`, `n_points_5m/10m/15m` |
| Гео (7) | `dist_to_target_m`, `route_dist_to_target_m`, `n_stops_to_target`, `route_over_straight_ratio`, `implied_speed_kmh`, `implied_speed_kmh_straight`, `speed_deficit_10m` |
| История (6) | `n_stops_passed`, `own_delay_last`, `own_delay_mean_last3`, `own_delay_trend`, `own_delay_mean_all`, `own_delay_std_all` |
| Peer (5) | `peer_speed_mean_target`, `peer_stuck_frac_target`, `peer_n_target`, `peer_speed_mean_here`, `peer_n_here` |
| Прочее (5) | `last_speed`, `last_heading`, `time_since_fix_s`, `cur_dev_trend`, `cur_dev_avg_last3` |

Итого 7+15+7+6+5+5 = 45. Список пересекается с признаками из `main.py` в
Части I этого README — это одни и те же признаки, просто здесь описаны с
точки зрения того, что реально строит `feature_builder.py` в проде на
потоковых данных, а не в оффлайн-обучении на CSV.

## 16. ML-модель

CatBoost Regressor, обучен на 100 000 примерах реального трафика в Москве.
MAE на офлайн-тесте — 55,3 секунды. Порядок признаков строго соответствует
`model_contract.json`, пересобирается в `ml_service/model.py`.

**Пороги `risk_level`:**

| Прогноз задержки | Уровень риска |
| --- | --- |
| > 120 сек | high |
| > 60 сек | medium |
| иначе | low |

**Fallback.** Если ML-сервис недоступен, воркер использует эвристику по
скорости и расписанию вместо модели.

**Метрики из `/worker/stats`:**

| Метрика | Смысл |
| --- | --- |
| `fallback_used` | Сколько прогнозов обработано эвристикой (при рабочей модели должно быть 0) |
| `crossings` | Количество зафиксированных прохождений остановок |
| `processed` | Всего обработано сообщений |

## 17. Производительность и надёжность

- Задержка ML-инференса: ~15 мс (по логам `ml_service`).
- Пропускная способность: 1 пакет в секунду на борт.
- Холодный старт Docker: ~30 секунд.
- Деградация при обрыве NDTP → переход на эвристику (проверено через
  `docker stop ndtp-emulator`).
- Восстановление после реконнекта — автоматическое, через heartbeat и
  `ensure_group`.
- Размер БД: ~50 МБ на 1 борт за 30 минут.

**Надёжность:**

- Redis Stream Consumer Groups с `XACK` — без потери сообщений при сбое
  воркера.
- `XAUTOCLAIM` — воркер забирает зависшие сообщения от упавших потребителей.
- `ProcessedStreamMessage` — таблица идемпотентности, защита от повторной
  обработки.
- Heartbeat — `/health` честно показывает состояние TCP-сервера.
- Fallback ML — воркер продолжает работу при недоступности ML-сервиса.

## 18. Тестирование

```powershell
cd "ВСТАВЬТЕ ВАШ ПУТЬ"
py -m pytest -v
```

Ожидаемо: 12–18 тестов, все `PASSED`.

**Что покрыто:**

- `test_parser.py` — NDTP-парсер (корректные realtime- и handshake-кадры,
  битый CRC, битая сигнатура, слишком короткие кадры, отрицательные
  координаты, граничные значения).
- `test_api.py` — REST API (`/health`, `/vehicles/`, `/predictions/`,
  авторизация 401 без токена, 404 для несуществующих ресурсов).

**Ручное тестирование:**

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/health"
Invoke-RestMethod -Uri "http://localhost:8001/api/v1/whatif/bus-1/options"
Invoke-RestMethod -Uri "http://localhost:8001/api/v1/whatif/bus-1?measure=extra_vehicle&count=2"
```

**Проверка деградации:**

```powershell
docker stop ndtp-emulator
Start-Sleep -Seconds 12
Invoke-RestMethod -Uri "http://localhost:8001/health"
# ожидаемо: ndtp_tcp false, всё остальное true

docker start ndtp-emulator
Start-Sleep -Seconds 15
Invoke-RestMethod -Uri "http://localhost:8001/health"
# ожидаемо: ndtp_tcp true
```

## 19. Решение проблем

**Порт уже занят:**

```powershell
cd "C:\Users\Стас\Desktop\Test final"
docker compose down
docker container prune -f
docker compose up -d
```

**Не удаётся разрешить имя хоста `postgres`:**

```powershell
docker compose down
docker network prune -f
docker compose up -d
```

**Ограничение `uq_stop_crossing_vehicle_stop_planned` не существует:**

```powershell
docker exec testfinal-postgres-1 psql -U postgres -d postgres -c "ALTER TABLE stop_crossings ADD CONSTRAINT uq_stop_crossing_vehicle_stop_planned UNIQUE (vehicle_id, stop_id, planned_time);"
docker restart testfinal-worker-1
```

**`fallback_used` растёт, `crossings` равно 0:**

```powershell
docker exec testfinal-postgres-1 psql -U postgres -d postgres -c "DELETE FROM schedules WHERE route_id = 'route-101';"
py seed_schedule.py
docker restart testfinal-worker-1
```

**Прогнозы всегда `low`, `probability` 20.** Это нормально для
синтетического эмулятора (задержки меньше 60 секунд). Для демонстрации
`medium` и `high` сдвиньте расписание на 8–15 минут назад:

```powershell
docker exec testfinal-postgres-1 psql -U postgres -d postgres -c "UPDATE schedules SET scheduled_arrival = scheduled_arrival - INTERVAL '8 minutes' WHERE route_id = 'route-101';"
docker restart testfinal-worker-1
```

**Ошибка CORS в браузере.** Добавить URL фронтенда в `ALLOWED_ORIGINS` в
`.env`:

```text
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
```

Пересобрать API:

```powershell
docker compose up -d --build api
```

**Фронтенд не подключается к API.** Проверить `frontend/.env`:

```text
VITE_API_URL=http://localhost:8001
```

Перезапустить `npm run dev` (Vite считывает `.env` только при запуске).

## 20. Стек технологий

- Веб-фреймворк — FastAPI 0.115+
- ORM — SQLAlchemy 2.0
- Миграции — Alembic
- БД — PostgreSQL 16
- Очередь и кэш — Redis 7 (Streams, ZSET)
- ML — CatBoost, pandas, numpy
- Авторизация — Bearer Token
- Контейнеризация — Docker, Docker Compose
- Python — 3.12
- Фронтенд — React 18, TypeScript, Vite

## 21. Ссылки

- Swagger API: `http://localhost:8001/docs`
- Дашборд: `http://localhost:5173`
- OpenAPI JSON: `http://localhost:8001/openapi.json`
