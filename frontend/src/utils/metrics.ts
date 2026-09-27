import type { CompactSnapshot, Filters, RiskLevel, RouteDef, Snapshot, Direction } from '../types';

export const riskLevel = (p: number): RiskLevel => (p >= 0.7 ? 'bad' : p >= 0.5 ? 'warn' : 'ok');
export const segmentLevel = (r: number): RiskLevel => (r >= 0.6 ? 'bad' : r >= 0.35 ? 'warn' : 'ok');

export const COLORS: Record<RiskLevel, string> = { ok: '#2FA86A', warn: '#F0A020', bad: '#E5484D' };

export interface Kpi {
  expected: number;
  sighted: number;
  onTime: number;
  atRisk: number;
}

/** Фильтр «сети»: тип маршрута, маршрут, направление. Применяется к карте, KPI и таблице. */
export function makeNetworkFilter(routes: RouteDef[], f: Filters) {
  const byId = new Map(routes.map((r) => [r.id, r]));
  return (routeId: string, dir: Direction) => {
    const r = byId.get(routeId);
    if (!r) return false;
    if (f.routeType !== 'all' && r.type !== f.routeType) return false;
    if (f.routeId && f.routeId !== routeId) return false;
    if (f.direction !== 'all' && String(dir) !== f.direction) return false;
    return true;
  };
}

export function toCompact(s: Snapshot): CompactSnapshot {
  return { ts: s.ts, v: s.vehicles.map((v) => ({ r: v.routeId, d: v.direction, s: v.status, p: v.pDelay })) };
}

export function kpiOf(c: CompactSnapshot, pass: (r: string, d: Direction) => boolean): Kpi {
  let expected = 0, sighted = 0, onTime = 0, atRisk = 0;
  for (const v of c.v) {
    if (!pass(v.r, v.d)) continue;
    expected++;
    if (v.s !== 'not_seen') {
      sighted++;
      if (v.s === 'on_time') onTime++;
      if (v.p >= 0.5) atRisk++;
    }
  }
  return { expected, sighted, onTime, atRisk };
}

/** Срез, ближайший к моменту «5 минут назад». null — если истории пока недостаточно. */
export function snapshotAgo(history: CompactSnapshot[], now: number, ms = 5 * 60_000): CompactSnapshot | null {
  const target = now - ms;
  let best: CompactSnapshot | null = null;
  for (const h of history) {
    if (h.ts <= target + 5_000) best = h;
    else break;
  }
  return best && Math.abs(best.ts - target) < 30_000 ? best : null;
}

export const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);
