import { useState } from 'react';
import { CAUSES } from '../data/causes';
import { COLORS, riskLevel } from '../utils/metrics';
import { Sparkline } from './Sparkline';
import type { Incident, RouteDef, Vehicle } from '../types';

interface ListProps {
  incidents: Incident[];
  vehicles: Map<string, Vehicle>;
  routes: Map<string, RouteDef>;
  selectedId: string | null;
  acknowledged: Set<string>;
  onSelect: (id: string | null) => void;
  onAck: (id: string) => void;
  onShowOnMap: (id: string) => void;
}
interface WhatIfResult {
  vehicle_id: string;
  route_id: string;
  found: boolean;
  extra_vehicles: number;
  reduction_pct: number;
  before: {
    delay_seconds: number;
    probability: number;
    risk_level: string;
  };
  after: {
    delay_seconds: number;
    probability: number;
    risk_level: string;
  };
}
export function IncidentList(p: ListProps) {
  if (!p.incidents.length) {
    return <div className="empty">Отклонений в горизонте 10–15 минут не прогнозируется</div>;
  }
  return (
    <div className="incidents">
      {p.incidents.map((inc) => (
        <IncidentCard
          key={inc.id}
          inc={inc}
          route={p.routes.get(inc.routeId)}
          vehicle={p.vehicles.get(inc.vehicleId)}
          selected={inc.id === p.selectedId}
          acked={p.acknowledged.has(inc.id)}
          onToggle={() => p.onSelect(inc.id === p.selectedId ? null : inc.id)}
          onAck={() => p.onAck(inc.id)}
          onShowOnMap={() => p.onShowOnMap(inc.id)}
        />
      ))}
    </div>
  );
}

interface CardProps {
  inc: Incident;
  route?: RouteDef;
  vehicle?: Vehicle;
  selected: boolean;
  acked: boolean;
  onToggle: () => void;
  onAck: () => void;
  onShowOnMap: () => void;
}

function IncidentCard({ inc, route, vehicle, selected, acked, onToggle, onAck, onShowOnMap }: CardProps) {
  const [whatIf, setWhatIf] = useState<string | null>(null);          // для рекомендаций
  const [whatIfResult, setWhatIfResult] = useState<WhatIfResult | null>(null);  // для кнопки +1 ТС
  const [whatIfLoading, setWhatIfLoading] = useState(false);

  const level = riskLevel(inc.pDelay);
  const color = COLORS[level];
  const p = Math.round(inc.pDelay * 100);

  const runWhatIf = async () => {
    setWhatIfLoading(true);
    try {
      const r = await fetch(
        `http://localhost:8001/api/v1/whatif/${inc.vehicleId}?extra_vehicles=1`
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setWhatIfResult(data);
    } catch (e) {
      console.error('whatif failed', e);
      setWhatIfResult(null);
    } finally {
      setWhatIfLoading(false);
    }
  };

  return (
    <article
      id={`card-${inc.id}`}
      className={`card card--${level} ${selected ? 'is-sel' : ''} ${acked ? 'is-acked' : ''}`}
      aria-expanded={selected}
    >
      <button className="card__summary" onClick={onToggle}>
        <div className="card__head">
          <div className="card__id">
            <div>
              <span className="rnum">{route?.num ?? inc.routeId}</span>
              <span className="card__bort">ТС {inc.vehicleId}</span>
            </div>
            <span className="card__dir">→ {inc.destination}</span>
          </div>
          <div className="card__prob">
            <b style={{ color }}>{p}%</b>
            <span>P задержки</span>
          </div>
        </div>
        <div className="card__pills">
          <span className="pill pill--sev">Опоздание <b>+{inc.predictedDelayMin} мин</b></span>
          <span className="pill">через ~<b>{inc.etaMin} мин</b></span>
          {acked && <span className="pill pill--work">В работе</span>}
        </div>
        <dl className="card__kv">
          <dt>Причина</dt>
          <dd>{CAUSES[inc.cause].label}</dd>
          <dt>Участок</dt>
          <dd>{inc.fromStop} → {inc.toStop}</dd>
        </dl>
      </button>

      {selected && (
        <div className="card__details">
          {vehicle && vehicle.history.length > 1 && (
            <div>
              <h4>Вероятность задержки · последние 2 мин</h4>
              <Sparkline values={[...vehicle.history, inc.pDelay]} color={color} />
            </div>
          )}
          <div>
            <h4>Рекомендации</h4>
            <div className="recs">
              {inc.recommendations.map((r) => {
                const open = whatIf === r.id;
                const np = Math.round(inc.pDelay * r.effect * 100);
                const nd = Math.max(0, Math.round(inc.predictedDelayMin * r.effect * 0.8));
                return (
                  <div className="rec" key={r.id}>
                    <div className="rec__row">
                      <span>{r.text}</span>
                      <button className="btn btn--sm" onClick={() => setWhatIf(open ? null : r.id)}>
                        {open ? 'Скрыть' : 'What-if'}
                      </button>
                    </div>
                    {open && (
                      <div className="rec__effect">
                        P: {p}% → <b>{np}%</b> · опоздание +{inc.predictedDelayMin} → <b>+{nd} мин</b>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="card__actions">
            <button className="btn btn--primary" onClick={onAck}>
              {acked ? 'Вернуть в очередь' : 'Принять в работу'}
            </button>
            <button className="btn" onClick={onShowOnMap}>Показать на карте</button>
            <button
              className="btn"
              onClick={runWhatIf}
              disabled={whatIfLoading}
            >
              {whatIfLoading ? 'Считаю…' : 'Что если +1 ТС?'}
            </button>
          </div>

          {whatIfResult && whatIfResult.found && (
            <div className="rec" style={{ marginTop: 8 }}>
              <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                <b>What-if: выпустить +{whatIfResult.extra_vehicles} ТС</b>
                <br />
                Задержка: <b>{whatIfResult.before.delay_seconds} → {whatIfResult.after.delay_seconds} сек</b>{' '}
                (−{whatIfResult.reduction_pct}%)
                <br />
                Риск: <b>{whatIfResult.before.risk_level} → {whatIfResult.after.risk_level}</b>
                <br />
                P задержки: <b>{whatIfResult.before.probability}% → {whatIfResult.after.probability}%</b>
              </div>
            </div>
          )}

          <p className="card__meta">ML: CatBoost · горизонт 10–15 мин</p>
        </div>
      )}
    </article>
  );
}