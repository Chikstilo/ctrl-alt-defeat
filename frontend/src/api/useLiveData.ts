import { useEffect, useRef, useState } from 'react';
import { Simulator } from '../sim/simulator';
import { ROUTES } from '../data/routes';
import { toCompact } from '../utils/metrics';
import type { CompactSnapshot, RouteDef, Snapshot } from '../types';

const API_URL = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '');
const HISTORY_MS = 6 * 60_000;

export interface LiveData {
  snapshot: Snapshot | null;
  history: CompactSnapshot[];
  routes: RouteDef[];
  source: 'demo' | 'api';
  error: string | null;
}

/**
 * Единая точка получения данных.
 * - VITE_API_URL задан → опрашиваем Backend (GET /api/v1/routes один раз, GET /api/v1/snapshot каждые 2 с).
 * - не задан → встроенный симулятор (тот же формат данных).
 */
export function useLiveData(): LiveData {
  const [state, setState] = useState<LiveData>({
    snapshot: null, history: [], routes: ROUTES, source: API_URL ? 'api' : 'demo', error: null,
  });
  const historyRef = useRef<CompactSnapshot[]>([]);

  const pushHistory = (s: Snapshot) => {
    const h = historyRef.current;
    h.push(toCompact(s));
    while (h.length && h[0].ts < s.ts - HISTORY_MS) h.shift();
    return [...h];
  };

  useEffect(() => {
    let stop = false;

    if (!API_URL) {
      const sim = new Simulator(ROUTES);
      historyRef.current = sim.warmup(330);
      const tick = () => {
        sim.step(1);
        const s = sim.snapshot();
        setState((prev) => ({ ...prev, snapshot: s, history: pushHistory(s) }));
      };
      tick();
      const id = window.setInterval(tick, 1000);
      return () => window.clearInterval(id);
    }

    (async () => {
      try {
        const r = await fetch(`${API_URL}/api/v1/routes`);
        if (r.ok) {
          const routes = (await r.json()) as RouteDef[];
          if (!stop) setState((p) => ({ ...p, routes }));
        }
      } catch {
        /* маршруты останутся локальными */
      }
    })();

    const poll = async () => {
      try {
        const r = await fetch(`${API_URL}/api/v1/snapshot`);
        if (!r.ok) throw new Error(`Backend ответил ${r.status}`);
        const s = (await r.json()) as Snapshot;
        if (!stop) setState((p) => ({ ...p, snapshot: s, history: pushHistory(s), error: null }));
      } catch (e) {
        if (!stop) setState((p) => ({ ...p, error: e instanceof Error ? e.message : 'Нет связи с Backend' }));
      }
    };
    poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  return state;
}

/** Подтверждение инцидента диспетчером (в демо-режиме — только локально) */
export async function acknowledgeIncident(id: string, ack: boolean) {
  if (!API_URL) return;
  try {
    await fetch(`${API_URL}/api/v1/incidents/${encodeURIComponent(id)}/ack`, {
      method: ack ? 'POST' : 'DELETE',
    });
  } catch {
    /* не критично для UI */
  }
}
