"""
Пайплайн предиктора задержек наземного транспорта.

Anti-leakage правило соблюдается ВЕЗДЕ: любая фича для точки (tr_id, T) использует
только event_time <= T (телеметрия) и time_begin/time_fact_begin <= T (расписание).

Геометрия — через pyproj (геодезические расстояния по WGS84, точнее haversine на
коротких дистанциях за счёт эллипсоида) и shapely (парсинг WKT "POINT (lon lat)").
Если библиотек нет — используется fallback на ручную математику (реализация и там,
и там даёт одинаковую по смыслу дистанцию в метрах, разница на городских масштабах
пренебрежима).
"""
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

print(">>> ЗАГРУЖЕН main.py ВЕРСИЯ С НОВЫМИ ФИЧАМИ (heading_change, n_stops_to_target, stop_geo_bucket) <<<")

# ---------------------------------------------------------------- geometry backend
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
    """Геодезическое расстояние в метрах между двумя точками (lon, lat)."""
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
    """
    Средний по модулю угол поворота между соседними замерами heading (градусы),
    С ПРАВИЛЬНОЙ обработкой перехода через 0/360 (иначе поворот 350 -> 10 градусов
    наивно считался бы как "разница 340", хотя реально это поворот всего на 20).
    """
    if len(h) < 2:
        return np.nan
    d = np.diff(h)
    d = (d + 180) % 360 - 180  # приводим к диапазону [-180, 180)
    return np.mean(np.abs(d))


# ---------------------------------------------------------------- loading
def load_traffic(path):
    """traffic.csv -> DataFrame только с валидными координатами, отсортирован по (tr_id, event_time)."""
    df = pd.read_csv(path, usecols=['tr_id', 'event_time', 'location_valid', 'lon', 'lat', 'speed', 'heading'],
                      parse_dates=['event_time'])
    df['location_valid'] = df['location_valid'].astype(str).str.lower() == 'true'
    df = df[df.location_valid].copy()
    df['speed'] = df['speed'].clip(0, 120)          # GPS-выбросы (встречаются значения >300 км/ч — шум)
    df = df.sort_values(['tr_id', 'event_time']).reset_index(drop=True)
    return df


def load_schedule(path, has_fact=True):
    """
    schedule.csv (has_fact=True, есть time_fact_begin) или
    schedule_plan.csv (has_fact=False, только план, для validate).
    Добавляет lon/lat (из geom) и delay_s = факт - план (если есть факт).
    """
    parse_cols = ['time_begin'] + (['time_fact_begin'] if has_fact else [])
    df = pd.read_csv(path, parse_dates=parse_cols)
    df['lon'], df['lat'] = zip(*df['geom'].map(parse_geom))
    df = df.sort_values(['tr_id', 'time_begin']).reset_index(drop=True)
    if has_fact:
        df['delay_s'] = (df['time_fact_begin'] - df['time_begin']).dt.total_seconds()
    return df


def load_labels(path):
    """labels_train.csv / labels_test.csv / points.csv -> с распарсенными датами T и target_time_begin."""
    df = pd.read_csv(path)
    df['T'] = pd.to_datetime(df['T'])
    df['target_time_begin'] = pd.to_datetime(df['target_time_begin'])
    return df


# ---------------------------------------------------------------- feature engine
class FeatureEngine:
    """
    Держит телеметрию + расписание в удобных для быстрого lookup структурах
    и строит фичи для набора прогнозных точек (labels/points), не заглядывая
    в будущее относительно T.

    Создаётся ОДИН РАЗ на (traffic, schedule) пару, затем .build(labels_df)
    можно вызывать сколько угодно раз на разных наборах прогнозных точек
    (train, test, validate — если они смотрят в одну и ту же телеметрию/расписание).
    """

    def __init__(self, traffic: pd.DataFrame, schedule: pd.DataFrame, stop_geom: dict):
        self.traffic = traffic
        self.schedule = schedule
        self.stop_geom = stop_geom  # dict: tt_action_item_id -> (lon, lat)

        # группировка по tr_id -> быстрый поиск "своей" истории через searchsorted
        self.tel_by_tr = {tr: g.reset_index(drop=True) for tr, g in traffic.groupby('tr_id')}
        self.sch_by_tr = {tr: g.reset_index(drop=True) for tr, g in schedule.groupby('tr_id')}
        # позиция каждого tt_action_item_id внутри ПОЛНОГО (по времени) списка остановок борта --
        # нужно, чтобы для двух остановок одного борта посчитать расстояние "по маршруту" (сумма
        # сегментов между последовательными остановками), а не по прямой. Это только ГЕОМЕТРИЯ
        # маршрута (позиции остановок) -- публичная инфраструктура, не факт о задержке, так что
        # использование ПОЛНОГО (в т.ч. будущего) списка остановок борта здесь не является утечкой.
        self._stop_pos_in_route = {}   # tt_action_item_id -> (tr_id, index within sch_by_tr[tr_id])
        for tr, g in self.sch_by_tr.items():
            for i, sid in enumerate(g['tt_action_item_id'].values):
                self._stop_pos_in_route[sid] = (tr, i)

        # одно общее kD-дерево по координатам ВСЕЙ телеметрии -> peer-фичи (cKDTree, как ты просил)
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
        """
        Расстояние ВДОЛЬ маршрута (по цепочке остановок), а не по прямой:
        от текущей позиции (lon0, lat0) до target_stop_id, идя по остановкам борта
        в ПОРЯДКЕ ВРЕМЕНИ, начиная с последней уже пройденной (anchor = по T, НЕ по
        геопоиску -- бот может проезжать похожие координаты несколько раз за смену
        на петлевом маршруте, геопоиск по всему дню путает "утренний" и "текущий" визит).

        Возвращает (dist_m, n_stops_between) - вторым элементом идёт число ещё не
        пройденных остановок ДО целевой включительно (грубая мера "сложности участка":
        больше остановок впереди -> больше шансов накопить/растерять задержку на
        посадке-высадке пассажиров).
        """
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

        # категориальные фичи оставляем как строки -- CatBoost сам с ними разберётся
        feats['tr_id_cat'] = str(tr_id)
        feats['target_stop_id_cat'] = str(target_stop_id)

        feats['cur_dev_s'] = cur_dev_s
        feats['time_to_target_s'] = (target_time_begin - T).total_seconds()
        feats['hour'] = T.hour
        feats['minute_of_day'] = T.hour * 60 + T.minute
        feats['dow'] = T.dayofweek

        # ---- 1. собственная телеметрия до T
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
                    # НОВОЕ: средний угол поворота за окно - маневрирование/пробка/
                    # интенсивный трафик со светофорами, а не просто "стоим/едем"
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

        # ---- 2. геометрия (pyproj/shapely): расстояние и требуемая скорость до цели
        tlon, tlat = self.stop_geom.get(target_stop_id, (np.nan, np.nan))
        if not np.isnan(lon0) and not np.isnan(tlon):
            dist_straight_m = distance_m(lon0, lat0, tlon, tlat)
            dist_route_m, n_stops_to_target = self.route_distance_to_target_m(tr_id, T, lon0, lat0, target_stop_id)
            # если маршрутное расстояние не удалось посчитать -- fallback на прямую
            dist_m = dist_route_m if not np.isnan(dist_route_m) else dist_straight_m

            feats['dist_to_target_m'] = dist_straight_m
            feats['route_dist_to_target_m'] = dist_route_m
            feats['n_stops_to_target'] = n_stops_to_target  # НОВОЕ - бесплатный побочный продукт
            # во сколько раз маршрут "длиннее" прямой -- сама по себе полезная фича (извилистость)
            feats['route_over_straight_ratio'] = dist_route_m / dist_straight_m if dist_straight_m > 10 else np.nan

            tt = max(feats['time_to_target_s'], 1.0)
            feats['implied_speed_kmh'] = dist_m / tt * 3.6          # по маршруту (основная версия)
            feats['implied_speed_kmh_straight'] = dist_straight_m / tt * 3.6  # по прямой (для сравнения)
            feats['speed_deficit_10m'] = feats['implied_speed_kmh'] - feats.get('speed_mean_10m', np.nan)

            # НОВОЕ: пространственный "бакет" целевой остановки - округляем координаты
            # до сетки ~50-100м. В отличие от target_stop_id_cat (почти уникален на
            # каждый рейс, риск переобучения - см. разбор с trip_id ранее), этот бакет
            # ПОВТОРЯЕТСЯ у разных tr_id, физически близких остановок и разных дней -
            # даёт модели шанс выучить "эта часть города хронически проблемная",
            # а не запомнить конкретный tt_action_item_id.
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

        # ---- 3. собственная история отклонений по уже пройденным (по плану) остановкам
        sch = self._own_schedule_upto(tr_id, T)
        if sch is not None and len(sch):
            feats['n_stops_passed'] = len(sch)
            if 'delay_s' in sch.columns:
                last3 = sch['delay_s'].tail(3).to_numpy()
                feats['own_delay_mean_last3'] = np.nanmean(last3)
                feats['own_delay_last'] = last3[-1]
                feats['own_delay_trend'] = last3[-1] - last3[0] if len(last3) >= 2 else 0.0
                feats['own_delay_mean_all'] = sch['delay_s'].mean()
                # НОВОЕ: насколько СТАБИЛЬНО ведёт себя борт по задержкам -
                # низкий std = можно доверять тренду, высокий = борт "дёрганый"
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

        # ---- 4. peer-фичи (cKDTree): что делают ДРУГИЕ борта физически рядом с целью прямо сейчас
        if not np.isnan(tlon):
            radius_deg = 0.008  # ~700-800 м на широте Москвы (расширено против предыдущей версии)
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

        # peer-фичи рядом с ТЕКУЩИМ положением борта
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
# категориальные фичи для CatBoost (используются в train_and_predict.py)
# target_stop_id_cat оставлен для сравнения (см. ablation-тест в объяснении) -
# если он окажется почти уникальным на каждую строку, стоит его убрать в пользу
# stop_geo_bucket, который обобщается лучше.
CAT_FEATURES = ['tr_id_cat', 'target_stop_id_cat', 'stop_geo_bucket']