# Построение 45 фич для CatBoost-модели из потоковых данных NDTP.
# own_delay_* теперь берутся из stop_crossings
# которые пишет worker.py при обнаружении борта в радиусе N метров от плановой остановки

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

import numpy as np

logger = logging.getLogger("feature_builder")

MSK_TZ = timezone(timedelta(hours=3))
BUFFER_MINUTES = 15
BUFFER_MAX_POINTS = 500
RADIUS_EARTH_M = 6_371_000

def haversine_m(lon1, lat1, lon2, lat2):

    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in [lon1, lat1, lon2, lat2]):
        return float("nan")
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * RADIUS_EARTH_M * asin(sqrt(a))


def circular_abs_diff_deg(h):
    if len(h) < 2:
        return float("nan")
    d = np.diff(h)
    d = (d + 180) % 360 - 180
    return float(np.mean(np.abs(d)))


class TelemetryBuffer:

    def __init__(self):
        self._by_vehicle: dict[str, deque] = {}
        self._by_route: dict[str, deque] = {}

    def add(self, vehicle_id: str, route_id: str, point: dict) -> None:
        if vehicle_id not in self._by_vehicle:
            self._by_vehicle[vehicle_id] = deque(maxlen=BUFFER_MAX_POINTS)
        self._by_vehicle[vehicle_id].append(point)

        if route_id not in self._by_route:
            self._by_route[route_id] = deque(maxlen=BUFFER_MAX_POINTS)
        self._by_route[route_id].append({**point, "vehicle_id": vehicle_id})

    def get_own_window(self, vehicle_id: str, end_time: datetime, minutes: int) -> list[dict]:
        buf = self._by_vehicle.get(vehicle_id)
        if not buf:
            return []
        cutoff = end_time - timedelta(minutes=minutes)
        return [p for p in buf if cutoff <= p["event_time"] <= end_time]

    def get_route_window(self, route_id: str, end_time: datetime, minutes: int,
                          exclude_vehicle_id: str | None = None) -> list[dict]:
        buf = self._by_route.get(route_id)
        if not buf:
            return []
        cutoff = end_time - timedelta(minutes=minutes)
        return [
            p for p in buf
            if cutoff <= p["event_time"] <= end_time
            and (exclude_vehicle_id is None or p["vehicle_id"] != exclude_vehicle_id)
        ]


class FeatureBuilder:

    def __init__(self):
        self.telemetry = TelemetryBuffer()

    def add_telemetry(self, vehicle_id: str, route_id: str, event_time: datetime,
                      lat: float, lon: float, speed: float, heading: float) -> None:
        self.telemetry.add(vehicle_id, route_id, {
            "event_time": event_time,
            "lat": lat, "lon": lon,
            "speed": speed, "heading": heading,
        })

    def build(self, vehicle_id: str, route_id: str, T: datetime, current: dict,
              schedule: list[dict], crossings: list[dict] | None = None) -> dict:
        """
        vehicle_id: str
        route_id: str
        T: aware datetime (MSK или UTC)
        current: {lat, lon, speed, heading, event_time}
        schedule: список {'stop_id', 'scheduled_arrival' (naive MSK), 'lat', 'lon'},
                  отсортирован по времени
        crossings: список {'delay_seconds', 'actual_time'} — от свежих к старым,
                   факты прохождения остановок из stop_crossings
        """
        feats: dict = {}
        if T.tzinfo is not None:
            T = T.astimezone(MSK_TZ).replace(tzinfo=None)

        horizon_start = T + timedelta(minutes=10)
        horizon_end = T + timedelta(minutes=15)
        candidates = [
            s for s in schedule
            if horizon_start < s["scheduled_arrival"] <= horizon_end
        ]
        past_stops = [s for s in schedule if s["scheduled_arrival"] <= T]

        if not candidates:
            return {}

        target = candidates[0]
        future_stops = [s for s in schedule if s["scheduled_arrival"] > T]
        tlat, tlon = target.get("lat"), target.get("lon")
        if tlat is None or tlon is None or np.isnan(tlat) or np.isnan(tlon):
            return {}

        feats["tr_id_cat"] = str(vehicle_id)
        feats["target_stop_id_cat"] = str(target["stop_id"])
        feats["stop_geo_bucket"] = f"{round(tlon, 3)}_{round(tlat, 3)}"
        feats["time_to_target_s"] = (target["scheduled_arrival"] - T).total_seconds()

        if past_stops:
            last_past = past_stops[-1]
            feats["cur_dev_s"] = (T - last_past["scheduled_arrival"]).total_seconds()
        else:
            feats["cur_dev_s"] = 0.0

        feats["hour"] = T.hour
        feats["minute_of_day"] = T.hour * 60 + T.minute
        feats["dow"] = T.weekday()

        if current and not np.isnan(current.get("lat", np.nan)):
            feats["last_speed"] = float(current.get("speed") or 0)
            feats["last_heading"] = float(current.get("heading") or 0)
            feats["time_since_fix_s"] = 0.0

            for w, name in [(5, "5m"), (10, "10m"), (15, "15m")]:
                win = self.telemetry.get_own_window(vehicle_id, T, w)
                if win:
                    speeds = np.array([p["speed"] for p in win], dtype=float)
                    headings = np.array([p["heading"] for p in win], dtype=float)
                    feats[f"speed_mean_{name}"] = float(np.nanmean(speeds))
                    feats[f"speed_std_{name}"] = float(np.nanstd(speeds)) if len(speeds) > 1 else float("nan")
                    feats[f"stuck_frac_{name}"] = float((speeds < 2).mean())
                    feats[f"n_points_{name}"] = len(win)
                    feats[f"heading_change_{name}"] = circular_abs_diff_deg(headings)
                else:
                    feats[f"speed_mean_{name}"] = float("nan")
                    feats[f"speed_std_{name}"] = float("nan")
                    feats[f"stuck_frac_{name}"] = float("nan")
                    feats[f"n_points_{name}"] = 0
                    feats[f"heading_change_{name}"] = float("nan")
        else:
            for k in ["last_speed", "last_heading", "time_since_fix_s"]:
                feats[k] = float("nan")
            for name in ["5m", "10m", "15m"]:
                feats[f"speed_mean_{name}"] = float("nan")
                feats[f"speed_std_{name}"] = float("nan")
                feats[f"stuck_frac_{name}"] = float("nan")
                feats[f"n_points_{name}"] = 0
                feats[f"heading_change_{name}"] = float("nan")

        if current and not np.isnan(current.get("lat", np.nan)):
            lon0, lat0 = current["lon"], current["lat"]
            dist_straight = haversine_m(lon0, lat0, tlon, tlat)
            feats["dist_to_target_m"] = dist_straight

            route_dist = dist_straight * 1.2 if not np.isnan(dist_straight) else float("nan")
            feats["route_dist_to_target_m"] = route_dist
            feats["n_stops_to_target"] = len([
                s for s in future_stops if s["scheduled_arrival"] <= target["scheduled_arrival"]
            ])
            feats["route_over_straight_ratio"] = (
                route_dist / dist_straight if dist_straight and dist_straight > 10 else float("nan")
            )

            tt = max(feats["time_to_target_s"], 1.0)
            feats["implied_speed_kmh"] = route_dist / tt * 3.6 if not np.isnan(route_dist) else float("nan")
            feats["implied_speed_kmh_straight"] = dist_straight / tt * 3.6 if not np.isnan(dist_straight) else float("nan")
            feats["speed_deficit_10m"] = feats["implied_speed_kmh"] - feats.get("speed_mean_10m", float("nan"))
        else:
            for k in ["dist_to_target_m", "route_dist_to_target_m", "n_stops_to_target",
                      "route_over_straight_ratio", "implied_speed_kmh",
                      "implied_speed_kmh_straight", "speed_deficit_10m"]:
                feats[k] = float("nan")

        feats["n_stops_passed"] = len(past_stops)

        if crossings:
            delays = [c["delay_seconds"] for c in crossings]
            delays_last3 = delays[:3]

            feats["own_delay_last"] = float(delays[0])
            feats["own_delay_mean_last3"] = float(np.mean(delays_last3))

            if len(delays) >= 2:
                feats["own_delay_trend"] = float(delays[0] - delays[-1])
            else:
                feats["own_delay_trend"] = 0.0

            feats["own_delay_mean_all"] = float(np.mean(delays))
            feats["own_delay_std_all"] = (
                float(np.std(delays)) if len(delays) > 1 else float("nan")
            )
        else:
            for k in ["own_delay_mean_last3", "own_delay_last", "own_delay_trend",
                      "own_delay_mean_all", "own_delay_std_all"]:
                feats[k] = float("nan")

        RADIUS_TARGET_M = 750.0
        RADIUS_HERE_M = 600.0

        route_win_target = self.telemetry.get_route_window(
            route_id, T, minutes=15, exclude_vehicle_id=vehicle_id)
        near_target = [
            p for p in route_win_target
            if haversine_m(p["lon"], p["lat"], tlon, tlat) <= RADIUS_TARGET_M
        ]
        if near_target:
            speeds = np.array([p["speed"] for p in near_target], dtype=float)
            feats["peer_speed_mean_target"] = float(np.nanmean(speeds))
            feats["peer_stuck_frac_target"] = float((speeds < 2).mean())
            feats["peer_n_target"] = len(near_target)
        else:
            feats["peer_speed_mean_target"] = float("nan")
            feats["peer_stuck_frac_target"] = float("nan")
            feats["peer_n_target"] = 0

        if current and not np.isnan(current.get("lat", np.nan)):
            lon0, lat0 = current["lon"], current["lat"]
            route_win_here = self.telemetry.get_route_window(
                route_id, T, minutes=5, exclude_vehicle_id=vehicle_id)
            near_here = [
                p for p in route_win_here
                if haversine_m(p["lon"], p["lat"], lon0, lat0) <= RADIUS_HERE_M
            ]
            if near_here:
                speeds_here = np.array([p["speed"] for p in near_here], dtype=float)
                feats["peer_speed_mean_here"] = float(np.nanmean(speeds_here))
                feats["peer_n_here"] = len(near_here)
            else:
                feats["peer_speed_mean_here"] = float("nan")
                feats["peer_n_here"] = 0
        else:
            feats["peer_speed_mean_here"] = float("nan")
            feats["peer_n_here"] = 0
        feats["cur_dev_trend"] = 0.0
        feats["cur_dev_avg_last3"] = feats["cur_dev_s"]

        return feats