import type { ReactNode } from 'react';
import { CAUSES, ALL_CAUSES } from '../data/causes';
import type { Filters, RouteDef } from '../types';

interface Props {
  routes: RouteDef[];
  filters: Filters;
  onChange: (f: Filters) => void;
}

export function FilterSidebar({ routes, filters, onChange }: Props) {
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => onChange({ ...filters, [k]: v });
  const visibleRoutes = routes.filter((r) => filters.routeType === 'all' || r.type === filters.routeType);
  const dirty = JSON.stringify(filters) !== JSON.stringify(DEFAULT_FILTERS);

  return (
    <aside className="sidebar" aria-label="Фильтры">
      <div className="sidebar__group">
        <h2 className="sidebar__heading">Сеть</h2>
        <Field id="f-type" label="Тип маршрута">
          <select id="f-type" value={filters.routeType}
            onChange={(e) => onChange({ ...filters, routeType: e.target.value as Filters['routeType'], routeId: '' })}>
            <option value="all">Все</option>
            <option value="rapid">Магистральные (м)</option>
            <option value="ring">Кольцевые</option>
          </select>
        </Field>
        <Field id="f-route" label="Маршрут">
          <select id="f-route" value={filters.routeId} onChange={(e) => set('routeId', e.target.value)}>
            <option value="">Все</option>
            {visibleRoutes.map((r) => (
              <option key={r.id} value={r.id}>{r.num} · {r.name}</option>
            ))}
          </select>
        </Field>
        <Field id="f-dir" label="Направление">
          <select id="f-dir" value={filters.direction} onChange={(e) => set('direction', e.target.value as Filters['direction'])}>
            <option value="all">Прямое и обратное</option>
            <option value="0">Прямое</option>
            <option value="1">Обратное</option>
          </select>
        </Field>
      </div>

      <div className="sidebar__group">
        <h2 className="sidebar__heading">Инциденты</h2>
        <Field id="f-risk" label="Уровень риска">
          <select id="f-risk" value={filters.risk} onChange={(e) => set('risk', e.target.value as Filters['risk'])}>
            <option value="all">Все (≥ 50%)</option>
            <option value="bad">Критичные (≥ 70%)</option>
            <option value="warn">Внимание (50–70%)</option>
          </select>
        </Field>
        <Field id="f-cause" label="Причина">
          <select id="f-cause" value={filters.cause} onChange={(e) => set('cause', e.target.value as Filters['cause'])}>
            <option value="all">Все</option>
            {ALL_CAUSES.map((c) => (
              <option key={c} value={c}>{CAUSES[c].short}</option>
            ))}
          </select>
        </Field>
      </div>

      <button className="btn btn--ghost sidebar__reset" disabled={!dirty} onClick={() => onChange(DEFAULT_FILTERS)}>
        Сбросить фильтры
      </button>

      <p className="sidebar__note">
        Прогноз строится на горизонте 10–15 минут. Фильтры «Сеть» влияют на карту, KPI и таблицу.
      </p>
    </aside>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
    </div>
  );
}

export const DEFAULT_FILTERS: Filters = { routeType: 'all', routeId: '', direction: 'all', risk: 'all', cause: 'all' };
