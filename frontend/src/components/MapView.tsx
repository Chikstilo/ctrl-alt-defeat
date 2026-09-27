import { useCallback, useEffect, useMemo, useState } from 'react';
import L from 'leaflet';
import { CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet';
import { CAUSES } from '../data/causes';
import { MOSCOW_CENTER } from '../data/routes';
import { BASEMAPS, type Basemap } from '../data/basemaps';
import { COLORS, riskLevel, segmentLevel } from '../utils/metrics';
import type { Incident, LatLng, RiskLevel, RouteDef, SegmentState, Vehicle } from '../types';

export interface FocusRequest {
  pos: LatLng;
  key: number;
}

interface Props {
  routes: RouteDef[];
  segments: SegmentState[];
  vehicles: Vehicle[];
  incidentsByVehicle: Map<string, Incident>;
  selected: Incident | null;
  focus: FocusRequest | null;
  fitRouteId: string;
  onVehicleClick: (vehicleId: string) => void;
}

const iconCache = new Map<string, L.DivIcon>();
function busIcon(num: string, level: RiskLevel, selected: boolean, offline: boolean) {
  const key = `${num}|${level}|${selected}|${offline}`;
  let icon = iconCache.get(key);
  if (!icon) {
    const w = Math.max(24, num.length * 8 + 12);
    icon = L.divIcon({
      className: 'bus-marker',
      html: `<div class="bus bus--${offline ? 'off' : level}${selected ? ' is-sel' : ''}">${num}</div>`,
      iconSize: [w, 20],
      iconAnchor: [w / 2, 10],
    });
    iconCache.set(key, icon);
  }
  return icon;
}

const MOSCOW_BOUNDS = L.latLngBounds([55.48, 37.2], [56.02, 38.05]);

export function MapView({ routes, segments, vehicles, incidentsByVehicle, selected, focus, fitRouteId, onVehicleClick }: Props) {
  const routeById = useMemo(() => new Map(routes.map((r) => [r.id, r])), [routes]);
  const visibleRouteIds = useMemo(() => new Set(vehicles.map((v) => v.routeId)), [vehicles]);
  const visibleSegments = segments.filter((s) => visibleRouteIds.has(s.routeId));
  const selSegment = selected ? segments.find((s) => s.id === selected.segmentId) : undefined;
  const [baseIdx, setBaseIdx] = useState(() => {
    try {
      const saved = localStorage.getItem('basemap');
      const i = BASEMAPS.findIndex((b) => b.id === saved);
      return i >= 0 ? i : 0;
    } catch {
      return 0;
    }
  });
  const [failed, setFailed] = useState<Set<string>>(new Set());
  const allFailed = failed.size >= BASEMAPS.length;

  const chooseBase = (i: number) => {
    setBaseIdx(i);
    try { localStorage.setItem('basemap', BASEMAPS[i].id); } catch { /* ignore */ }
  };
  // стабильная ссылка, чтобы таймер проверки в BaseLayer не перезапускался на каждом тике данных
  const onBaseFail = useCallback((id: string) => {
    setFailed((prev) => new Set(prev).add(id));
  }, []);
  useEffect(() => {
    if (!failed.has(BASEMAPS[baseIdx].id)) return;
    const nextIdx = BASEMAPS.findIndex((b) => !failed.has(b.id));
    if (nextIdx >= 0) setBaseIdx(nextIdx);
  }, [failed, baseIdx]);

  return (
    <div className="map">
      <MapContainer
        center={MOSCOW_CENTER}
        zoom={12}
        minZoom={10}
        maxBounds={MOSCOW_BOUNDS}
        zoomControl={false}
        preferCanvas={false}
        className="map__leaflet"
      >
        {!allFailed && <BaseLayer key={BASEMAPS[baseIdx].id} base={BASEMAPS[baseIdx]} onFail={onBaseFail} />}
        <Controller focus={focus} fitRouteId={fitRouteId} routes={routes} />

        {/* подложка-«обводка» под линиями */}
        {visibleSegments.map((s) => (
          <Polyline key={`c-${s.id}`} positions={s.coords} interactive={false}
            pathOptions={{ color: '#ffffff', weight: 10, opacity: 0.95, lineCap: 'round' }} />
        ))}
        {visibleSegments.filter((s) => segmentLevel(s.risk) === 'bad').map((s) => (
          <Polyline key={`g-${s.id}`} positions={s.coords} interactive={false}
            pathOptions={{ color: COLORS.bad, weight: 20, opacity: 0.28, className: 'seg-glow', lineCap: 'round' }} />
        ))}
        {visibleSegments.map((s) => {
          const lvl = segmentLevel(s.risk);
          const r = routeById.get(s.routeId);
          return (
            <Polyline key={`s-${s.id}`} positions={s.coords}
              pathOptions={{ color: COLORS[lvl], weight: 6, opacity: 1, lineCap: 'round' }}>
              <Tooltip sticky>
                <b>{r?.num}</b> · {s.from} → {s.to}<br />
                Риск на участке: <b>{Math.round(s.risk * 100)}%</b><br />
                {lvl === 'ok' ? 'Движение по графику' : CAUSES[s.cause].label}
              </Tooltip>
            </Polyline>
          );
        })}
        {selSegment && (
          <Polyline positions={selSegment.coords} interactive={false}
            pathOptions={{ color: '#1d1a17', weight: 3, dashArray: '2 8', opacity: 0.9, lineCap: 'round' }} />
        )}

        <StopsLayer routes={routes.filter((r) => visibleRouteIds.has(r.id))} />

        {vehicles.map((v) => {
          const r = routeById.get(v.routeId);
          const inc = incidentsByVehicle.get(v.id);
          const isSel = selected?.vehicleId === v.id;
          return (
            <Marker key={v.id} position={v.pos}
              icon={busIcon(r?.num ?? v.routeId, riskLevel(v.pDelay), isSel, v.status === 'not_seen')}
              zIndexOffset={isSel ? 1000 : inc ? 500 : 0}
              eventHandlers={{ click: () => onVehicleClick(v.id) }}>
              <Tooltip direction="top" offset={[0, -10]}>
                <b>{r?.num}</b> · ТС {v.id}<br />
                → {v.destination}<br />
                {v.status === 'not_seen' ? 'Нет связи' : (
                  <>P задержки: <b>{Math.round(v.pDelay * 100)}%</b> · отклонение {v.deviationMin >= 0 ? '+' : ''}{v.deviationMin.toFixed(1)} мин</>
                )}
              </Tooltip>
            </Marker>
          );
        })}
      </MapContainer>

      <div className="map__base">
        <label htmlFor="basemap">Подложка</label>
        <select id="basemap" value={baseIdx} onChange={(e) => { setFailed(new Set()); chooseBase(Number(e.target.value)); }}>
          {BASEMAPS.map((b, i) => (
            <option key={b.id} value={i}>{b.name}{failed.has(b.id) ? ' — недоступна' : ''}</option>
          ))}
        </select>
      </div>
      {allFailed && (
        <div className="map__warn" role="status">
          Не удалось загрузить карту ни с одного сервера. Проверьте интернет или VPN и выберите подложку заново.
        </div>
      )}

      <div className="map__legend">
        <div>
          <h3>Участок</h3>
          <p><i className="ln" style={{ background: COLORS.ok }} />Норма</p>
          <p><i className="ln" style={{ background: COLORS.warn }} />Риск отклонения</p>
          <p><i className="ln" style={{ background: COLORS.bad }} />Прогнозируется сбой</p>
        </div>
        <div>
          <h3>ТС · P задержки</h3>
          <p><i className="dt" style={{ background: COLORS.ok }} />&lt; 50%</p>
          <p><i className="dt" style={{ background: COLORS.warn }} />50–70%</p>
          <p><i className="dt" style={{ background: COLORS.bad }} />≥ 70%</p>
          <p><i className="dt dt--off" />Нет связи</p>
        </div>
      </div>
    </div>
  );
}

/**
 * Слой тайлов с проверкой доступности: если за 7 с не пришло ни одного тайла
 * или подряд идут ошибки — сообщаем наверх, и MapView переключает источник.
 */
function BaseLayer({ base, onFail }: { base: Basemap; onFail: (id: string) => void }) {
  useEffect(() => {
    let loaded = 0;
    let errors = 0;
    let done = false;
    const fail = () => {
      if (done || loaded > 0) return;
      done = true;
      console.warn(`[map] подложка «${base.name}» недоступна, переключаюсь`);
      onFail(base.id);
    };
    const timer = window.setTimeout(fail, 7000);
    const onLoad = () => { loaded++; };
    const onError = () => { errors++; if (errors >= 6) fail(); };
    window.addEventListener(`tile:${base.id}:load`, onLoad);
    window.addEventListener(`tile:${base.id}:error`, onError);
    return () => {
      done = true;
      window.clearTimeout(timer);
      window.removeEventListener(`tile:${base.id}:load`, onLoad);
      window.removeEventListener(`tile:${base.id}:error`, onError);
    };
  }, [base, onFail]);

  return (
    <TileLayer
      url={base.url}
      attribution={base.attribution}
      subdomains={base.subdomains ?? 'abc'}
      maxZoom={base.maxZoom}
      eventHandlers={{
        tileload: () => window.dispatchEvent(new Event(`tile:${base.id}:load`)),
        tileerror: () => window.dispatchEvent(new Event(`tile:${base.id}:error`)),
      }}
    />
  );
}

function Controller({ focus, fitRouteId, routes }: { focus: FocusRequest | null; fitRouteId: string; routes: RouteDef[] }) {
  const map = useMap();

  useEffect(() => {
    const zoomCtl = L.control.zoom({ position: 'topright', zoomInTitle: 'Приблизить', zoomOutTitle: 'Отдалить' }).addTo(map);
    // без анимации маркеров во время зума, иначе они «съезжают»
    const el = map.getContainer();
    const on = () => el.classList.add('is-zooming');
    const off = () => window.setTimeout(() => el.classList.remove('is-zooming'), 80);
    map.on('zoomstart', on);
    map.on('zoomend', off);
    return () => {
      map.off('zoomstart', on);
      map.off('zoomend', off);
      zoomCtl.remove();
    };
  }, [map]);

  useEffect(() => {
    if (focus) map.flyTo(focus.pos, Math.max(map.getZoom(), 14), { duration: 0.8 });
  }, [focus?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const r = routes.find((x) => x.id === fitRouteId);
    if (r) map.flyToBounds(L.latLngBounds(r.stops.map((s) => s.pos)), { padding: [40, 40], duration: 0.8 });
    else map.flyTo(MOSCOW_CENTER, 12, { duration: 0.8 });
  }, [fitRouteId]); // eslint-disable-line react-hooks/exhaustive-deps

  return null;
}

function StopsLayer({ routes }: { routes: RouteDef[] }) {
  const map = useMap();
  const [zoom, setZoom] = useState(map.getZoom());
  useMapEvents({ zoomend: () => setZoom(map.getZoom()) });
  if (zoom < 13) return null;
  return (
    <>
      {routes.flatMap((r) =>
        r.stops.map((s, i) => (
          <CircleMarker key={`${r.id}-${i}`} center={s.pos} radius={4}
            pathOptions={{ color: '#5b544d', weight: 1.5, fillColor: '#ffffff', fillOpacity: 1 }}>
            <Tooltip direction="right" offset={[6, 0]} permanent={zoom >= 15}>{s.name}</Tooltip>
          </CircleMarker>
        )),
      )}
    </>
  );
}
