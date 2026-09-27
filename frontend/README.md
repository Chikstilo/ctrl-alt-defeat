# Пульт диспетчера · Москва

BI-дашборд диспетчера для кейса «Предиктор изменений в графике движения городского транспорта».
React 18 + TypeScript + Vite + Leaflet (карта OpenStreetMap / CARTO).

Что на экране:

- **Фильтры** (слева): тип маршрута, маршрут, направление, уровень риска, причина.
- **KPI**: рейсов по плану / на линии / по графику / риск задержки, с дельтой «vs 5 мин назад».
- **Инциденты**: ТС с вероятностью задержки ≥ 50% в горизонте 10–15 мин. В карточке — прогноз опоздания,
  время до события, причина, участок, график вероятности, рекомендации с What-if, «Принять в работу».
- **Маршруты**: таблица по маршрутам (рейсы, по графику, %, раньше, позже, нет связи, риск), сортировка по клику на заголовок, клик по строке показывает маршрут на карте.
- **Карта**: линии маршрутов по участкам (зелёный / жёлтый / красный), ТС с номером маршрута, остановки при приближении.

Без Backend дашборд работает на встроенном демо-симуляторе, который отдаёт данные в том же формате, что и API.

---

## 1. Установка

Нужен **Node.js 20+** (проверить: `node -v`). Скачать: https://nodejs.org (LTS).

```bash
cd transit-dashboard
npm install        # ставит все зависимости из package.json
npm run dev        # запуск на http://localhost:5173
```

Остальные команды:

```bash
npm run build      # проверка типов + production-сборка в dist/
npm run preview    # запуск собранной версии на http://localhost:4173
npm run typecheck  # только проверка типов
```

Зависимости (ставятся сами через `npm install`):

| Пакет | Зачем |
|---|---|
| react, react-dom 18.3 | UI |
| leaflet 1.9 + react-leaflet 4.2 | карта |
| vite 5, @vitejs/plugin-react | сборка и dev-сервер |
| typescript 5.5, @types/* | типизация |

> Карте нужен интернет: тайлы грузятся с `basemaps.cartocdn.com`, шрифты — с Google Fonts.

## 2. Подключение к Backend

Скопируйте `.env.example` в `.env` и укажите адрес сервиса:

```
VITE_API_URL=http://localhost:8000
```

Перезапустите `npm run dev`. В шапке бейдж «Демо» сменится на «Live».
Backend должен разрешить CORS для адреса фронтенда (в FastAPI — `CORSMiddleware`).

### Контракт API

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/v1/routes` | `RouteDef[]` — маршруты и остановки (один раз при старте) |
| GET | `/api/v1/snapshot` | `Snapshot` — опрашивается каждые 2 с |
| POST / DELETE | `/api/v1/incidents/{id}/ack` | принять инцидент в работу / вернуть |

Все типы описаны в `src/types.ts`. Координаты — `[lat, lon]`. Пример `Snapshot`:

```json
{
  "ts": 1790446481000,
  "vehicles": [{
    "id": "041203", "routeId": "m2", "direction": 0, "destination": "Библиотека им. Ленина",
    "pos": [55.7391, 37.5260], "segmentIndex": 1, "aheadSegmentIndex": 2,
    "pDelay": 0.82, "deviationMin": 2.4, "status": "on_time", "history": [0.61, 0.66, 0.74]
  }],
  "segments": [{
    "id": "m2:2", "routeId": "m2", "index": 2, "from": "Кутузовская", "to": "Дорогомиловская застава",
    "coords": [[55.7404, 37.5342], [55.744, 37.551]], "risk": 0.8, "cause": "dtp"
  }],
  "incidents": [{
    "id": "inc-041203", "vehicleId": "041203", "routeId": "m2", "direction": 0,
    "destination": "Библиотека им. Ленина", "pDelay": 0.82, "predictedDelayMin": 7, "etaMin": 12,
    "cause": "dtp", "segmentId": "m2:2", "fromStop": "Кутузовская", "toStop": "Дорогомиловская застава",
    "recommendations": [{ "id": "dtp-0", "text": "Согласовать объезд по дублёру", "effect": 0.45 }]
  }]
}
```

- `status`: `early | on_time | late | not_seen`
- `cause`: `dtp | jam | works | light | bunch | tpu` (подписи — в `src/data/causes.ts`)
- `effect` — множитель вероятности после применения меры (для What-if)
- `segments[].coords` может содержать сколько угодно точек: сюда стоит отдавать геометрию из GTFS `shapes.txt`
  после map matching, тогда линии лягут точно по улицам.

## 3. Docker

```bash
docker build -t dispatch-dashboard --build-arg VITE_API_URL=http://localhost:8000 .
docker run -p 8080:80 dispatch-dashboard
# открыть http://localhost:8080
```

Для docker-compose вместе с Backend и ML-модулем:

```yaml
services:
  dashboard:
    build:
      context: ./transit-dashboard
      args:
        VITE_API_URL: http://localhost:8000   # адрес, доступный из браузера
    ports: ["8080:80"]
```

## 4. Структура

```
src/
  api/useLiveData.ts      получение данных: Backend или симулятор
  sim/simulator.ts        демо-поток телеметрии и «ML-выход»
  data/routes.ts          демо-маршруты Москвы (координаты условные)
  data/causes.ts          причины сбоев и рекомендации
  utils/metrics.ts        KPI, пороги риска, цвета, фильтры
  utils/geo.ts            расстояния, интерполяция, ГПСЧ
  components/
    Header.tsx            шапка: регион, часы, статус Live/Демо
    FilterSidebar.tsx     фильтры
    KpiPanel.tsx          KPI с дельтой «vs 5 мин назад»
    IncidentList.tsx      карточки инцидентов + What-if
    RouteTable.tsx        таблица по маршрутам
    MapView.tsx           карта Leaflet: участки, ТС, остановки, легенда
    Sparkline.tsx         график вероятности
  App.tsx                 компоновка и состояние
  styles.css              стили
```

Пороги: ТС — жёлтый от 50%, красный от 70% вероятности задержки; участок — жёлтый от 35%, красный от 60% риска.
Меняются в `src/utils/metrics.ts`.
