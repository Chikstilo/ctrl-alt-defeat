import { useMemo, useState } from 'react';
import type { RouteDef, Vehicle } from '../types';
import { pct } from '../utils/metrics';

interface Props {
  routes: RouteDef[];
  vehicles: Vehicle[];
  selectedRouteId: string;
  onSelectRoute: (id: string) => void;
}

type Row = { id: string; num: string; name: string; trips: number; onTime: number; share: number; early: number; late: number; notSeen: number; risk: number };
type Key = Exclude<keyof Row, 'id' | 'name'>;

const COLS: { key: Key; label: string; title?: string }[] = [
  { key: 'num', label: 'Маршрут' },
  { key: 'trips', label: 'Рейсы' },
  { key: 'onTime', label: 'По графику' },
  { key: 'share', label: '%' },
  { key: 'early', label: 'Раньше' },
  { key: 'late', label: 'Позже' },
  { key: 'notSeen', label: 'Нет связи' },
  { key: 'risk', label: 'Риск', title: 'ТС с вероятностью задержки ≥ 50%' },
];

export function RouteTable({ routes, vehicles, selectedRouteId, onSelectRoute }: Props) {
  const [sort, setSort] = useState<{ key: Key; desc: boolean }>({ key: 'share', desc: true });

  const rows = useMemo(() => {
    const out: Row[] = routes.map((r) => {
      const vs = vehicles.filter((v) => v.routeId === r.id);
      const seen = vs.filter((v) => v.status !== 'not_seen');
      const onTime = seen.filter((v) => v.status === 'on_time').length;
      return {
        id: r.id, num: r.num, name: r.name, trips: vs.length, onTime,
        share: pct(onTime, seen.length),
        early: seen.filter((v) => v.status === 'early').length,
        late: seen.filter((v) => v.status === 'late').length,
        notSeen: vs.length - seen.length,
        risk: seen.filter((v) => v.pDelay >= 0.5).length,
      };
    }).filter((r) => r.trips > 0);
    const k = sort.key;
    out.sort((a, b) => {
      const va = a[k], vb = b[k];
      const c = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb), 'ru');
      return sort.desc ? -c : c;
    });
    return out;
  }, [routes, vehicles, sort]);

  const total = rows.reduce(
    (t, r) => ({ trips: t.trips + r.trips, onTime: t.onTime + r.onTime, early: t.early + r.early, late: t.late + r.late, notSeen: t.notSeen + r.notSeen, risk: t.risk + r.risk }),
    { trips: 0, onTime: 0, early: 0, late: 0, notSeen: 0, risk: 0 },
  );

  return (
    <div className="table-wrap">
      <table className="rtable">
        <thead>
          <tr>
            {COLS.map((c) => (
              <th key={c.key} title={c.title} aria-sort={sort.key === c.key ? (sort.desc ? 'descending' : 'ascending') : 'none'}>
                <button onClick={() => setSort((s) => ({ key: c.key, desc: s.key === c.key ? !s.desc : true }))}>
                  {c.label}
                  {sort.key === c.key && <i aria-hidden="true">{sort.desc ? '▾' : '▴'}</i>}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={selectedRouteId === r.id ? 'is-sel' : ''}
              onClick={() => onSelectRoute(selectedRouteId === r.id ? '' : r.id)} title={r.name}>
              <td><span className="rnum">{r.num}</span></td>
              <td>{r.trips}</td>
              <td>{r.onTime}</td>
              <td>
                <span className="share">
                  <span className="share__bar"><i style={{ width: `${r.share}%` }} className={r.share >= 70 ? 'ok' : r.share >= 50 ? 'warn' : 'bad'} /></span>
                  {r.share}%
                </span>
              </td>
              <td>{r.early || ''}</td>
              <td className={r.late ? 't-bad' : ''}>{r.late || ''}</td>
              <td>{r.notSeen || ''}</td>
              <td>{r.risk ? <span className="riskdot">{r.risk}</span> : ''}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td>Итого</td>
            <td>{total.trips}</td>
            <td>{total.onTime}</td>
            <td>{pct(total.onTime, total.trips - total.notSeen)}%</td>
            <td>{total.early}</td>
            <td>{total.late}</td>
            <td>{total.notSeen}</td>
            <td>{total.risk}</td>
          </tr>
        </tfoot>
      </table>
      <p className="table-hint">Клик по строке — показать маршрут на карте</p>
    </div>
  );
}
