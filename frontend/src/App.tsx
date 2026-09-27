import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLiveData, acknowledgeIncident } from './api/useLiveData';
import { Header } from './components/Header';
import { FilterSidebar, DEFAULT_FILTERS } from './components/FilterSidebar';
import { KpiPanel } from './components/KpiPanel';
import { RouteTable } from './components/RouteTable';
import { IncidentList } from './components/IncidentList';
import { MapView, type FocusRequest } from './components/MapView';
import { kpiOf, makeNetworkFilter, snapshotAgo, toCompact } from './utils/metrics';
import type { Filters, Incident } from './types';

const timeFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit', second: '2-digit' });

export default function App() {
  const { snapshot, history, routes, source, error } = useLiveData();
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [tab, setTab] = useState<'incidents' | 'routes'>('incidents');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<FocusRequest | null>(null);

  const pass = useMemo(() => makeNetworkFilter(routes, filters), [routes, filters]);
  const routeById = useMemo(() => new Map(routes.map((r) => [r.id, r])), [routes]);

  const vehicles = useMemo(
    () => (snapshot ? snapshot.vehicles.filter((v) => pass(v.routeId, v.direction)) : []),
    [snapshot, pass],
  );
  const vehicleById = useMemo(() => new Map(vehicles.map((v) => [v.id, v])), [vehicles]);

  const kpiNow = useMemo(() => (snapshot ? kpiOf(toCompact(snapshot), pass) : null), [snapshot, pass]);
  const kpiPrev = useMemo(() => {
    if (!snapshot) return null;
    const ago = snapshotAgo(history, snapshot.ts);
    return ago ? kpiOf(ago, pass) : null;
  }, [snapshot, history, pass]);

  const incidents = useMemo(() => {
    if (!snapshot) return [] as Incident[];
    return snapshot.incidents
      .filter((i) => pass(i.routeId, i.direction))
      .filter((i) => filters.risk === 'all' || (filters.risk === 'bad' ? i.pDelay >= 0.7 : i.pDelay < 0.7))
      .filter((i) => filters.cause === 'all' || i.cause === filters.cause)
      .sort((a, b) => Number(acknowledged.has(a.id)) - Number(acknowledged.has(b.id)) || b.pDelay - a.pDelay);
  }, [snapshot, pass, filters.risk, filters.cause, acknowledged]);

  const incidentsByVehicle = useMemo(
    () => new Map((snapshot?.incidents ?? []).map((i) => [i.vehicleId, i])),
    [snapshot],
  );
  const selected = incidents.find((i) => i.id === selectedId) ?? null;
  const tableRoutes = routes.filter((r) => pass(r.id, 0) || pass(r.id, 1));

  // при первой загрузке раскрываем самый критичный инцидент
  useEffect(() => {
    if (!selectedId && incidents[0] && !acknowledged.size) setSelectedId(incidents[0].id);
  }, [incidents.length > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  const focusVehicle = useCallback((vehicleId: string) => {
    const v = snapshot?.vehicles.find((x) => x.id === vehicleId);
    if (v) setFocus({ pos: v.pos, key: Date.now() });
  }, [snapshot]);

  const selectIncident = (id: string | null) => {
    setSelectedId(id);
    const inc = id ? incidents.find((i) => i.id === id) : null;
    if (inc) focusVehicle(inc.vehicleId);
  };

  const onVehicleClick = (vehicleId: string) => {
    const inc = incidentsByVehicle.get(vehicleId);
    if (!inc) return;
    setTab('incidents');
    setSelectedId(inc.id);
    window.setTimeout(() => document.getElementById(`card-${inc.id}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 50);
  };

  const toggleAck = (id: string) => {
    setAcknowledged((prev) => {
      const next = new Set(prev);
      const ack = !next.has(id);
      if (ack) next.add(id); else next.delete(id);
      void acknowledgeIncident(id, ack);
      return next;
    });
  };

  const critical = incidents.filter((i) => i.pDelay >= 0.7 && !acknowledged.has(i.id)).length;

  return (
    <div className="app">
      <Header source={source} error={error} />
      <FilterSidebar routes={routes} filters={filters} onChange={setFilters} />

      <main className="center">
        {kpiNow ? <KpiPanel now={kpiNow} prev={kpiPrev} /> : <div className="kpi kpi--loading">Загрузка телеметрии…</div>}

        <section className="board">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'incidents'} onClick={() => setTab('incidents')}>
              Инциденты
              <span className={`count ${critical ? 'count--bad' : ''}`}>{incidents.length}</span>
            </button>
            <button role="tab" aria-selected={tab === 'routes'} onClick={() => setTab('routes')}>
              Маршруты
            </button>
            <span className="tabs__hint">горизонт 10–15 мин</span>
          </div>
          <div className="board__body">
            {tab === 'incidents' ? (
              <IncidentList
                incidents={incidents}
                vehicles={vehicleById}
                routes={routeById}
                selectedId={selectedId}
                acknowledged={acknowledged}
                onSelect={selectIncident}
                onAck={toggleAck}
                onShowOnMap={(id) => {
                  const inc = incidents.find((i) => i.id === id);
                  if (inc) focusVehicle(inc.vehicleId);
                }}
              />
            ) : (
              <RouteTable
                routes={tableRoutes}
                vehicles={vehicles}
                selectedRouteId={filters.routeId}
                onSelectRoute={(id) => setFilters((f) => ({ ...f, routeId: id }))}
              />
            )}
          </div>
        </section>
      </main>

      <section className="mapcol" aria-label="Карта">
        <MapView
          routes={routes}
          segments={snapshot?.segments ?? []}
          vehicles={vehicles}
          incidentsByVehicle={incidentsByVehicle}
          selected={selected}
          focus={focus}
          fitRouteId={filters.routeId}
          onVehicleClick={onVehicleClick}
        />
      </section>

      <footer className="footer">
        Данные: GTFS / GTFS-RT · {source === 'demo' ? 'демо-симулятор (координаты условные)' : 'Backend-сервис'}
        {snapshot && <> · обновлено {timeFmt.format(new Date(snapshot.ts))} МСК</>}
      </footer>
    </div>
  );
}
