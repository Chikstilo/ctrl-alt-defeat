import type { Kpi } from '../utils/metrics';
import { pct } from '../utils/metrics';

interface Props {
  now: Kpi;
  prev: Kpi | null;
}

/** Блок KPI в стиле «Trips Expected / Sighted / On Time» + прогноз риска */
export function KpiPanel({ now, prev }: Props) {
  const rows: { label: string; value: number; share?: number; prevShare?: number; goodUp?: boolean }[] = [
    { label: 'Рейсов по плану', value: now.expected },
    {
      label: 'На линии', value: now.sighted, share: pct(now.sighted, now.expected),
      prevShare: prev ? pct(prev.sighted, prev.expected) : undefined, goodUp: true,
    },
    {
      label: 'По графику', value: now.onTime, share: pct(now.onTime, now.sighted),
      prevShare: prev ? pct(prev.onTime, prev.sighted) : undefined, goodUp: true,
    },
    {
      label: 'Риск задержки', value: now.atRisk, share: pct(now.atRisk, now.sighted),
      prevShare: prev ? pct(prev.atRisk, prev.sighted) : undefined, goodUp: false,
    },
  ];

  return (
    <section className="kpi" aria-label="Ключевые показатели">
      <div className="kpi__head">
        <span />
        <span>ТС</span>
        <span>%</span>
        <span>vs 5 мин назад</span>
      </div>
      {rows.map((r) => {
        const delta = r.share !== undefined && r.prevShare !== undefined ? r.share - r.prevShare : null;
        const good = delta === null || delta === 0 ? null : (delta > 0) === r.goodUp;
        return (
          <div className="kpi__row" key={r.label}>
            <span className="kpi__label">{r.label}</span>
            <span className="kpi__value">{r.value}</span>
            <span className="kpi__share">{r.share !== undefined ? `${r.share}%` : ''}</span>
            <span className={`kpi__delta ${good === null ? '' : good ? 'is-good' : 'is-bad'}`}>
              {r.share === undefined ? '' : delta === null ? '—' : (
                <>
                  <i aria-hidden="true">{delta > 0 ? '▲' : delta < 0 ? '▼' : '•'}</i>
                  {Math.abs(delta)} п.п.
                </>
              )}
            </span>
          </div>
        );
      })}
    </section>
  );
}
