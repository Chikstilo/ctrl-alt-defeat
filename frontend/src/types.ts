/** [широта, долгота] — порядок как в Leaflet */
export type LatLng = [number, number];

export type RiskLevel = 'ok' | 'warn' | 'bad';
export type CauseCode = 'dtp' | 'jam' | 'works' | 'light' | 'bunch' | 'tpu';
export type VehicleStatus = 'early' | 'on_time' | 'late' | 'not_seen';
export type Direction = 0 | 1;
export type RouteType = 'rapid' | 'ring';

export interface Stop {
  name: string;
  pos: LatLng;
}

export interface RouteDef {
  id: string;
  num: string;
  name: string;
  type: RouteType;
  /** кольцевой маршрут: последний сегмент замыкается на первую остановку */
  closed?: boolean;
  stops: Stop[];
}

export interface SegmentState {
  id: string; // `${routeId}:${index}`
  routeId: string;
  index: number;
  from: string;
  to: string;
  coords: LatLng[];
  /** вероятность сбоя на участке в горизонте 10–15 мин, 0..1 */
  risk: number;
  cause: CauseCode;
}

export interface Vehicle {
  id: string;
  routeId: string;
  direction: Direction;
  destination: string;
  pos: LatLng;
  segmentIndex: number;
  aheadSegmentIndex: number;
  /** вероятность задержки, 0..1 */
  pDelay: number;
  /** текущее отклонение от графика, мин (+ опоздание, − опережение) */
  deviationMin: number;
  status: VehicleStatus;
  /** история pDelay (шаг 3 с) для спарклайна */
  history: number[];
}

export interface Recommendation {
  id: string;
  text: string;
  /** множитель к вероятности после применения меры (what-if) */
  effect: number;
}

export interface Incident {
  id: string;
  vehicleId: string;
  routeId: string;
  direction: Direction;
  destination: string;
  pDelay: number;
  predictedDelayMin: number;
  /** через сколько минут ожидается событие (10–15) */
  etaMin: number;
  cause: CauseCode;
  segmentId: string;
  fromStop: string;
  toStop: string;
  recommendations: Recommendation[];
}

export interface Snapshot {
  ts: number;
  vehicles: Vehicle[];
  segments: SegmentState[];
  incidents: Incident[];
}

/** Сжатый срез для расчёта динамики KPI («vs 5 мин назад») */
export interface CompactSnapshot {
  ts: number;
  v: { r: string; d: Direction; s: VehicleStatus; p: number }[];
}

export interface Filters {
  routeType: 'all' | RouteType;
  routeId: string; // '' = все
  direction: 'all' | '0' | '1';
  risk: 'all' | 'warn' | 'bad';
  cause: 'all' | CauseCode;
}
