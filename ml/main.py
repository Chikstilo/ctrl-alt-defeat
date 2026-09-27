
# Пайплайн предиктора задержек наземного транспорта.
# Anti-leakage правило соблюдается везде: любая фича для точки (tr_id, T) использует
# только event_time <= T и time_begin/time_fact_begin <= T
# Геометрия — через pyproj (геодезические расстояния по WGS84, haversine на
# коротких дистанциях за счёт эллипсоида) и shapely (парсинг WKT "POINT (lon lat)").
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

try:
    from shapely import wkt as _wkt
    from pyproj import Geod
    _GEOD = Geod(ellps='WGS84')
    _HAS_GEO_LIBS = True
except ImportError:
    _HAS_GEO_LIBS = False


def parse_geom(s):
    """'POINT (lon lat)' -> (lon, lat)."""
    if _HAS_GEO_LIBS:
        try:
            p = _wkt.loads(s)
            return p.x, p.y
        except Exception:
            return np.nan, np.nan
    else:
        import re
        m = re.match(r'POINT \(([-\d.]+) ([-\d.]+)\)', str(s))
        if not m:
            return np.nan, np.nan
        return float(m.group(1)), float(m.group(2))
def distance_m(lon1, lat1, lon2, lat2):
    #Геодезическое расстояние в метрах между двумя точками (lon, lat)
    if _HAS_GEO_LIBS:
        _, _, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
        return dist
    else:
        R = 6371000.0
        lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
        dlon, dlat = lon2 - lon1, lat2 - lat1
        a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
        return 2 * R * np.arcsin(np.sqrt(a))
def circular_abs_diff_deg(h):
    #Средний по модулю угол поворота между соседними замерами heading
    if len(h) < 2:
        return np.nan
    d = np.diff(h)
    d = (d + 180) % 360 - 180  
    return np.mean(np.abs(d))

def load_traffic(path):
    df = pd.read_csv(path, usecols=['tr_id', 'event_time', 'location_valid', 'lon', 'lat', 'speed', 'heading'],
                      parse_dates=['event_time'])
    df['location_valid'] = df['location_valid'].astype(str).str.lower() == 'true'
    df = df[df.location_valid].copy()
    df['speed'] = df['speed'].clip(0, 120)          
    df = df.sort_values(['tr_id', 'event_time']).reset_index(drop=True)
    return df


def load_schedule(path, has_fact=True):
    parse_cols = ['time_begin'] + (['time_fact_begin'] if has_fact else [])
    df = pd.read_csv(path, parse_dates=parse_cols)
    df['lon'], df['lat'] = zip(*df['geom'].map(parse_geom))
    df = df.sort_values(['tr_id', 'time_begin']).reset_index(drop=True)
    if has_fact:
        df['delay_s'] = (df['time_fact_begin'] - df['time_begin']).dt.total_seconds()
    return df

def load_labels(path):
    df = pd.read_csv(path)
    df['T'] = pd.to_datetime(df['T'])
    df['target_time_begin'] = pd.to_datetime(df['target_time_begin'])
    return df

class FeatureEngine:
    # Держит телеметрию и расписание в удобных для быстрого lookup структурах
    # и строит фичи для набора прогнозных точек не заглядывая
    # в будущее относительно T.
    
    def __init__(self, traffic: pd.DataFrame, schedule: pd.DataFrame, stop_geom: dict):
        self.traffic = traffic
        self.schedule = schedule
        self.stop_geom = stop_geom  
        self.tel_by_tr = {tr: g.reset_index(drop=True) for tr, g in traffic.groupby('tr_id')}
        self.sch_by_tr = {tr: g.reset_index(drop=True) for tr, g in schedule.groupby('tr_id')}
        self._stop_pos_in_route = {}  
        for tr, g in self.sch_by_tr.items():
            for i, sid in enumerate(g['tt_action_item_id'].values):
                self._stop_pos_in_route[sid] = (tr, i)
        coords = traffic[['lon', 'lat']].to_numpy()
        self.kdtree = cKDTree(coords)
        self.kd_tr = traffic['tr_id'].to_numpy()
        self.kd_time = traffic['event_time'].to_numpy()
        self.kd_speed = traffic['speed'].to_numpy()

    def _own_telemetry_upto(self, tr_id, T):
        g = self.tel_by_tr.get(tr_id)
        if g is None:
            return g
        idx = np.searchsorted(g['event_time'].values, np.datetime64(T), side='right')
        return g.iloc[:idx]

    def _own_schedule_upto(self, tr_id, T, time_col='time_begin'):
        g = self.sch_by_tr.get(tr_id)
        if g is None:
            return g
        idx = np.searchsorted(g[time_col].values, np.datetime64(T), side='right')
        return g.iloc[:idx]

    def route_distance_to_target_m(self, tr_id, T, lon0, lat0, target_stop_id):
        pos = self._stop_pos_in_route.get(target_stop_id)
        if pos is None or np.isnan(lon0):
            return np.nan, np.nan
        route_tr, target_idx = pos
        if route_tr != tr_id:
            return np.nan, np.nan
        g = self.sch_by_tr[tr_id]

        # anchor = индекс последней уже пройденной по расписанию остановки (time_begin <= T)
        anchor_idx = int(np.searchsorted(g['time_begin'].values, np.datetime64(T), side='right')) - 1
        next_idx = anchor_idx + 1  # первая ещё не пройденная остановка

        if next_idx > target_idx:
            # target уже "в прошлом" по индексу -- аномалия данных, fallback на прямую
            fallback = distance_m(lon0, lat0, *self.stop_geom.get(target_stop_id, (np.nan, np.nan)))
            return fallback, np.nan

        lons, lats = g['lon'].values, g['lat'].values
        dist = distance_m(lon0, lat0, lons[next_idx], lats[next_idx])
        for i in range(next_idx, target_idx):
            dist += distance_m(lons[i], lats[i], lons[i + 1], lats[i + 1])
        n_stops_between = target_idx - next_idx + 1
        return dist, n_stops_between

    def row_features(self, tr_id, T, target_stop_id, target_time_begin, cur_dev_s):
        feats = {}
        T = pd.Timestamp(T)
        target_time_begin = pd.Timestamp(target_time_begin)
        feats['tr_id_cat'] = str(tr_id)
        feats['target_stop_id_cat'] = str(target_stop_id)
        feats['cur_dev_s'] = cur_dev_s
        feats['time_to_target_s'] = (target_time_begin - T).total_seconds()
        feats['hour'] = T.hour
        feats['minute_of_day'] = T.hour * 60 + T.minute
        feats['dow'] = T.dayofweek

        tel = self._own_telemetry_upto(tr_id, T)
        if tel is not None and len(tel):
            last = tel.iloc[-1]
            lon0, lat0 = last['lon'], last['lat']
            feats['time_since_fix_s'] = (T - last['event_time']).total_seconds()
            feats['last_speed'] = last['speed']
            feats['last_heading'] = last['heading']

            for w, name in [(5, '5m'), (10, '10m'), (15, '15m')]:
                win = tel[tel['event_time'] >= T - pd.Timedelta(minutes=w)]
                if len(win):
                    feats[f'speed_mean_{name}'] = win['speed'].mean()
                    feats[f'speed_std_{name}'] = win['speed'].std()
                    feats[f'stuck_frac_{name}'] = (win['speed'] < 2).mean()
                    feats[f'n_points_{name}'] = len(win)
                    feats[f'heading_change_{name}'] = circular_abs_diff_deg(win['heading'].to_numpy())
                else:
                    feats[f'speed_mean_{name}'] = np.nan
                    feats[f'speed_std_{name}'] = np.nan
                    feats[f'stuck_frac_{name}'] = np.nan
                    feats[f'n_points_{name}'] = 0
                    feats[f'heading_change_{name}'] = np.nan
        else:
            lon0 = lat0 = np.nan
            for k in ['time_since_fix_s', 'last_speed', 'last_heading']:
                feats[k] = np.nan
            for name in ['5m', '10m', '15m']:
                feats[f'speed_mean_{name}'] = np.nan
                feats[f'speed_std_{name}'] = np.nan
                feats[f'stuck_frac_{name}'] = np.nan
                feats[f'n_points_{name}'] = 0
                feats[f'heading_change_{name}'] = np.nan

        #Расстояние и требуемая скорость до цели
        tlon, tlat = self.stop_geom.get(target_stop_id, (np.nan, np.nan))
        if not np.isnan(lon0) and not np.isnan(tlon):
            dist_straight_m = distance_m(lon0, lat0, tlon, tlat)
            dist_route_m, n_stops_to_target = self.route_distance_to_target_m(tr_id, T, lon0, lat0, target_stop_id)
            dist_m = dist_route_m if not np.isnan(dist_route_m) else dist_straight_m

            feats['dist_to_target_m'] = dist_straight_m
            feats['route_dist_to_target_m'] = dist_route_m
            feats['n_stops_to_target'] = n_stops_to_target
            # во сколько раз маршрут длиннее прямой
            feats['route_over_straight_ratio'] = dist_route_m / dist_straight_m if dist_straight_m > 10 else np.nan
            tt = max(feats['time_to_target_s'], 1.0)
            feats['implied_speed_kmh'] = dist_m / tt * 3.6          
            feats['implied_speed_kmh_straight'] = dist_straight_m / tt * 3.6 
            feats['speed_deficit_10m'] = feats['implied_speed_kmh'] - feats.get('speed_mean_10m', np.nan)
            feats['stop_geo_bucket'] = f"{round(tlon, 3)}_{round(tlat, 3)}"
        else:
            feats['dist_to_target_m'] = np.nan
            feats['route_dist_to_target_m'] = np.nan
            feats['n_stops_to_target'] = np.nan
            feats['route_over_straight_ratio'] = np.nan
            feats['implied_speed_kmh'] = np.nan
            feats['implied_speed_kmh_straight'] = np.nan
            feats['speed_deficit_10m'] = np.nan
            feats['stop_geo_bucket'] = "unknown"

        #собственная история отклонений по уже пройденным остановкам
        sch = self._own_schedule_upto(tr_id, T)
        if sch is not None and len(sch):
            feats['n_stops_passed'] = len(sch)
            if 'delay_s' in sch.columns:
                last3 = sch['delay_s'].tail(3).to_numpy()
                feats['own_delay_mean_last3'] = np.nanmean(last3)
                feats['own_delay_last'] = last3[-1]
                feats['own_delay_trend'] = last3[-1] - last3[0] if len(last3) >= 2 else 0.0
                feats['own_delay_mean_all'] = sch['delay_s'].mean()
                feats['own_delay_std_all'] = sch['delay_s'].std()
            else:
                feats['own_delay_mean_last3'] = np.nan
                feats['own_delay_last'] = np.nan
                feats['own_delay_trend'] = np.nan
                feats['own_delay_mean_all'] = np.nan
                feats['own_delay_std_all'] = np.nan
        else:
            feats['n_stops_passed'] = 0
            feats['own_delay_mean_last3'] = np.nan
            feats['own_delay_last'] = np.nan
            feats['own_delay_trend'] = np.nan
            feats['own_delay_mean_all'] = np.nan
            feats['own_delay_std_all'] = np.nan

        #Что делают другие борта физически рядом с целью прямо сейчас
        if not np.isnan(tlon):
            radius_deg = 0.008
            idxs = self.kdtree.query_ball_point([tlon, tlat], r=radius_deg)
            if idxs:
                idxs = np.array(idxs)
                mask_time = (self.kd_time[idxs] <= np.datetime64(T)) & \
                            (self.kd_time[idxs] >= np.datetime64(T - pd.Timedelta(minutes=15)))
                mask_other = self.kd_tr[idxs] != tr_id
                sel = idxs[mask_time & mask_other]
                if len(sel):
                    feats['peer_speed_mean_target'] = self.kd_speed[sel].mean()
                    feats['peer_stuck_frac_target'] = (self.kd_speed[sel] < 2).mean()
                    feats['peer_n_target'] = len(sel)
                else:
                    feats['peer_speed_mean_target'] = np.nan
                    feats['peer_stuck_frac_target'] = np.nan
                    feats['peer_n_target'] = 0
            else:
                feats['peer_speed_mean_target'] = np.nan
                feats['peer_stuck_frac_target'] = np.nan
                feats['peer_n_target'] = 0
        else:
            feats['peer_speed_mean_target'] = np.nan
            feats['peer_stuck_frac_target'] = np.nan
            feats['peer_n_target'] = 0

        # peer-фичи рядом с текущим положением борта
        if not np.isnan(lon0):
            idxs = self.kdtree.query_ball_point([lon0, lat0], r=0.006)
            if idxs:
                idxs = np.array(idxs)
                mask_time = (self.kd_time[idxs] <= np.datetime64(T)) & \
                            (self.kd_time[idxs] >= np.datetime64(T - pd.Timedelta(minutes=5)))
                mask_other = self.kd_tr[idxs] != tr_id
                sel = idxs[mask_time & mask_other]
                if len(sel):
                    feats['peer_speed_mean_here'] = self.kd_speed[sel].mean()
                    feats['peer_n_here'] = len(sel)
                else:
                    feats['peer_speed_mean_here'] = np.nan
                    feats['peer_n_here'] = 0
            else:
                feats['peer_speed_mean_here'] = np.nan
                feats['peer_n_here'] = 0
        else:
            feats['peer_speed_mean_here'] = np.nan
            feats['peer_n_here'] = 0

        return feats

    def build(self, labels_df):
        rows = [self.row_features(r.tr_id, r.T, r.target_stop_id, r.target_time_begin, r.cur_dev_s)
                for r in labels_df.itertuples(index=False)]
        feat_df = pd.DataFrame(rows)
        feat_df.insert(0, 'sample_id', labels_df['sample_id'].values)
        return feat_df

from catboost import Pool
def predict_one(model,feature_cols,cat_features,feature_engine, tr_id, T,target_stop_id,target_time_begin, cur_dev_s):
    feats = feature_engine.row_features(tr_id,T,target_stop_id,target_time_begin,cur_dev_s)
    row_df=pd.DataFrame([feats])[feature_cols]
    cat_idx= [feature_cols.index(c) for c in cat_features]
    pool = Pool(row_df,cat_features=cat_idx)
    return float(model.predict(pool)[0])
CAT_FEATURES = ['tr_id_cat', 'target_stop_id_cat', 'stop_geo_bucket']