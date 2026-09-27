/**
 * Источники подложки карты. Если текущий не отвечает, MapView автоматически
 * переключается на следующий. Порядок — от самого надёжного в РФ.
 */
export interface Basemap {
  id: string;
  name: string;
  url: string;
  subdomains?: string;
  attribution: string;
  maxZoom: number;
}

export const BASEMAPS: Basemap[] = [
  {
    id: 'osm',
    name: 'OpenStreetMap',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  },
  {
    id: '2gis',
    name: '2ГИС',
    url: 'https://tile{s}.maps.2gis.com/tiles?x={x}&y={y}&z={z}&v=1',
    subdomains: '0123',
    attribution: '&copy; <a href="https://2gis.ru">2ГИС</a>',
    maxZoom: 18,
  },
  {
    id: 'osm-fr',
    name: 'OSM France',
    url: 'https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png',
    subdomains: 'abc',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> France',
    maxZoom: 19,
  },
  {
    id: 'carto',
    name: 'CARTO Voyager',
    url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    subdomains: 'abcd',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 19,
  },
];
