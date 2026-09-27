import { ROUTES, DEMO_HOTSPOTS } from '../data/routes';
import { CAUSES, MINOR_CAUSES, ALL_CAUSES } from '../data/causes';
import { clamp, distKm, lerp, makeRng } from '../utils/geo';
import { toCompact } from '../utils/metrics';
import type {
  CauseCode, CompactSnapshot, Direction, Incident, LatLng, RouteDef, SegmentState, Snapshot, Vehicle, VehicleStatus,
} from '../types';

/**
 * Демо-симулятор потока телеметрии и «ML-выхода».
 * Отдаёт данные ровно в том формате, который должен отдавать Backend (см. README → «Контракт API»),
 * поэтому замена на реальный сервис не требует правок в UI.
 */

interface Geo { pts: LatLng[]; cum: number[]; L: number; closed: boolean; segCount: number }
interface SimSeg { id: string; routeId: string; index: number; base: number; risk: number; cause: CauseCode; hold: number }
interface SimVeh {
  id: string; route: RouteDef; geo: Geo; ringDir: Direction; s: number; speedK: number; bias: number;
  p: number; dev: number; hist: number[]; offlineFor: number;
}
interface Loc { pos: LatLng; i: number; dir: Direction; ahead: number; distToAhead: number }

const SPEED_KM_S = 0.03; // ускорено ~×6 для наглядности демо

export class Simulator {
  private rnd = makeRng(20260926);
  private geos = new Map<string, Geo>();
  private segs = new Map<string, SimSeg>();
  private vehicles: SimVeh[] = [];
  private t = 0;
  private nextEvent = 30;
  private now = Date.now();

  constructor(private routes: RouteDef[] = ROUTES) {
    for (const r of routes) {
      const pts = r.stops.map((s) => s.pos);
      if (r.closed) pts.push(pts[0]);
      const cum = [0];
      for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + distKm(pts[i - 1], pts[i]));
      const geo: Geo = { pts, cum, L: cum[cum.length - 1], closed: !!r.closed, segCount: pts.length - 1 };
      this.geos.set(r.id, geo);

      const hot = new Map((DEMO_HOTSPOTS[r.id] ?? []).map(([i, risk, c]) => [i, { risk, c }]));
      for (let i = 0; i < geo.segCount; i++) {
        const h = hot.get(i);
        const base = h ? h.risk : 0.08 + this.rnd() * 0.2;
        this.segs.set(`${r.id}:${i}`, {
          id: `${r.id}:${i}`, routeId: r.id, index: i, base, risk: base,
          cause: h ? h.c : MINOR_CAUSES[Math.floor(this.rnd() * MINOR_CAUSES.length)], hold: 0,
        });
      }

      const n = Math.max(5, Math.round(geo.L * (r.closed ? 0.9 : 0.55)));
      const loop = r.closed ? geo.L : 2 * geo.L;
      for (let k = 0; k < n; k++) {
        this.vehicles.push({
          id: String(100000 + this.vehicles.length * 1373 + Math.floor(this.rnd() * 900)).slice(0, 6),
          route: r, geo, ringDir: (k % 2) as Direction,
          s: ((k / n) * loop + this.rnd() * 0.3) % loop,
          speedK: 0.85 + this.rnd() * 0.3,
          bias: this.rnd() < 0.22 ? 0.06 + this.rnd() * 0.12 : -0.08 + this.rnd() * 0.1,
          p: 0, dev: 0, hist: [], offlineFor: 0,
        });
      }
    }
    // одно ТС «не на связи» с самого начала
    this.vehicles[7].offlineFor = 400;
    for (const v of this.vehicles) {
      const l = this.locate(v);
      v.p = this.targetP(v, l);
      v.dev = this.targetDev(v, l);
    }
  }

  private seg(routeId: string, i: number) {
    return this.segs.get(`${routeId}:${i}`)!;
  }

  private locate(v: SimVeh): Loc {
    const g = v.geo;
    let d: number, dir: Direction;
    if (g.closed) {
      dir = v.ringDir;
      d = dir === 0 ? v.s : g.L - v.s;
    } else {
      dir = v.s < g.L ? 0 : 1;
      d = dir === 0 ? v.s : 2 * g.L - v.s;
    }
    d = clamp(d, 0, g.L);
    let i = 0;
    while (i < g.segCount - 1 && d > g.cum[i + 1]) i++;
    const len = g.cum[i + 1] - g.cum[i] || 1;
    const pos = lerp(g.pts[i], g.pts[i + 1], (d - g.cum[i]) / len);
    let ahead = dir === 0 ? i + 1 : i - 1;
    ahead = g.closed ? (ahead + g.segCount) % g.segCount : clamp(ahead, 0, g.segCount - 1);
    const distToAhead = dir === 0 ? g.cum[i + 1] - d : d - g.cum[i];
    return { pos, i, dir, ahead, distToAhead };
  }

  private targetP(v: SimVeh, l: Loc) {
    const cur = this.seg(v.route.id, l.i).risk;
    const ahead = this.seg(v.route.id, l.ahead).risk;
    return clamp(0.3 * cur + 0.8 * ahead - 0.05 + v.bias + Math.max(0, v.dev) * 0.02 + (this.rnd() - 0.5) * 0.05, 0.02, 0.97);
  }

  private targetDev(v: SimVeh, l: Loc) {
    return this.seg(v.route.id, l.i).risk * 7 - 0.9 + v.bias * 12 + (this.rnd() - 0.5) * 0.6;
  }

  /** Шаг симуляции, dt в секундах */
  step(dt = 1) {
    this.t += dt;
    this.now += dt * 1000;
    const decay = 1 - Math.pow(0.96, dt);

    for (const s of this.segs.values()) {
      if (s.hold > 0) s.hold -= dt;
      else s.risk += (s.base - s.risk) * decay;
      s.risk = clamp(s.risk + (this.rnd() - 0.5) * 0.025 * Math.sqrt(dt), 0.03, 0.95);
    }

    this.nextEvent -= dt;
    if (this.nextEvent <= 0) {
      const all = [...this.segs.values()];
      const s = all[Math.floor(this.rnd() * all.length)];
      s.risk = 0.72 + this.rnd() * 0.18;
      s.hold = 40 + this.rnd() * 40;
      s.cause = ALL_CAUSES[Math.floor(this.rnd() * ALL_CAUSES.length)];
      this.nextEvent = 25 + this.rnd() * 30;
    }

    const pushHist = Math.floor(this.t / 3) !== Math.floor((this.t - dt) / 3);
    for (const v of this.vehicles) {
      const l = this.locate(v);
      const risk = this.seg(v.route.id, l.i).risk;
      const loop = v.geo.closed ? v.geo.L : 2 * v.geo.L;
      v.s = (v.s + SPEED_KM_S * v.speedK * (1 - 0.55 * risk) * dt) % loop;
      const l2 = this.locate(v);
      v.p += (this.targetP(v, l2) - v.p) * (1 - Math.pow(0.82, dt));
      v.dev += (this.targetDev(v, l2) - v.dev) * (1 - Math.pow(0.95, dt));
      if (v.offlineFor > 0) v.offlineFor -= dt;
      else if (this.rnd() < 0.00015 * dt) v.offlineFor = 45 + this.rnd() * 60;
      if (pushHist) {
        v.hist.push(v.p);
        if (v.hist.length > 40) v.hist.shift();
      }
    }
  }

  /** Прогоняет симуляцию «в прошлое», чтобы сразу были история спарклайнов и дельты «vs 5 мин» */
  warmup(seconds = 330): CompactSnapshot[] {
    const hist: CompactSnapshot[] = [];
    const end = Date.now();
    this.now = end - seconds * 1000;
    for (let i = 0; i < seconds; i++) {
      this.step(1);
      hist.push(toCompact(this.snapshot()));
    }
    this.now = end;
    return hist;
  }

  snapshot(): Snapshot {
    const vehicles: Vehicle[] = [];
    const incidents: Incident[] = [];
    for (const v of this.vehicles) {
      const l = this.locate(v);
      const r = v.route;
      const offline = v.offlineFor > 0;
      const status: VehicleStatus = offline ? 'not_seen' : v.dev > 3 ? 'late' : v.dev < -1 ? 'early' : 'on_time';
      const destination = r.closed
        ? l.dir === 0 ? 'по часовой' : 'против часовой'
        : l.dir === 0 ? r.stops[r.stops.length - 1].name : r.stops[0].name;
      vehicles.push({
        id: v.id, routeId: r.id, direction: l.dir, destination, pos: l.pos,
        segmentIndex: l.i, aheadSegmentIndex: l.ahead, pDelay: v.p, deviationMin: v.dev, status, history: [...v.hist],
      });

      if (!offline && v.p >= 0.5) {
        const seg = this.seg(r.id, l.ahead);
        const a = r.stops[seg.index].name;
        const b = r.stops[(seg.index + 1) % r.stops.length].name;
        const cause: CauseCode = seg.risk >= 0.35 ? seg.cause : 'bunch';
        incidents.push({
          id: `inc-${v.id}`,
          vehicleId: v.id,
          routeId: r.id,
          direction: l.dir,
          destination,
          pDelay: v.p,
          predictedDelayMin: Math.max(2, Math.round(v.p * 9 + seg.risk * 3 - 1 + Math.max(0, v.dev) * 0.4)),
          etaMin: clamp(10 + Math.round(l.distToAhead / 0.45), 10, 15),
          cause,
          segmentId: seg.id,
          fromStop: l.dir === 0 ? a : b,
          toStop: l.dir === 0 ? b : a,
          recommendations: CAUSES[cause].recs.map(([text, effect], i) => ({ id: `${cause}-${i}`, text, effect })),
        });
      }
    }

    const segments: SegmentState[] = [...this.segs.values()].map((s) => {
      const r = this.routes.find((x) => x.id === s.routeId)!;
      const g = this.geos.get(s.routeId)!;
      return {
        id: s.id, routeId: s.routeId, index: s.index,
        from: r.stops[s.index].name, to: r.stops[(s.index + 1) % r.stops.length].name,
        coords: [g.pts[s.index], g.pts[s.index + 1]], risk: s.risk, cause: s.cause,
      };
    });

    return { ts: this.now, vehicles, segments, incidents };
  }
}
