import type { LatLng } from '../types';

const KX = Math.cos((55.75 * Math.PI) / 180) * 111.32;
const KY = 111.32;

/** Расстояние в км (эквидистантная проекция — достаточно точно в пределах города) */
export const distKm = (a: LatLng, b: LatLng) => Math.hypot((b[1] - a[1]) * KX, (b[0] - a[0]) * KY);

export const lerp = (a: LatLng, b: LatLng, t: number): LatLng => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];

export const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

/** Детерминированный ГПСЧ (mulberry32), чтобы демо выглядело одинаково при каждом запуске */
export function makeRng(seed: number) {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
