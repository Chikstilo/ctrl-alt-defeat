import os
import sys
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.model_selection import GroupShuffleSplit

from main import load_traffic, load_schedule, load_labels, FeatureEngine, CAT_FEATURES


def get_data_path():
    if len(sys.argv) > 1:
        return sys.argv[1].strip().strip('"')
    return input('Путь к папке с данными (например D:\\dataset): ').strip().strip('"')


def to_pool(df, feature_cols, target=None):
    cat_idx = [feature_cols.index(c) for c in CAT_FEATURES]
    return Pool(df[feature_cols], label=target, cat_features=cat_idx)


def main():
    up = get_data_path()

    traffic_tr = load_traffic(os.path.join(up, 'train', 'traffic.csv'))
    schedule_tr = load_schedule(os.path.join(up, 'train', 'schedule.csv'), has_fact=True)
    stop_geom_tr = {row.tt_action_item_id: (row.lon, row.lat) for row in schedule_tr.itertuples()}
    lt = load_labels(os.path.join(up, 'labels', 'labels_train.csv'))

    fe = FeatureEngine(traffic_tr, schedule_tr, stop_geom_tr)
    X = fe.build(lt)
    X['target_delay_s'] = lt['target_delay_s'].values

    feature_cols = [c for c in X.columns if c not in ('sample_id', 'target_delay_s')]

    gss = GroupShuffleSplit(n_splits=1, test_size=1/3, random_state=42)
    fit_idx, holdout_idx = next(gss.split(X, groups=X['tr_id_cat']))
    Xfit, Xhold = X.iloc[fit_idx].reset_index(drop=True), X.iloc[holdout_idx].reset_index(drop=True)

    fit_vehicles = set(Xfit['tr_id_cat'])
    hold_vehicles = set(Xhold['tr_id_cat'])
    print(f'Бортов в fit: {len(fit_vehicles)}, в holdout: {len(hold_vehicles)}')
    print(f'Пересечение бортов между fit и holdout: {len(fit_vehicles & hold_vehicles)} (должно быть 0)')
    print(f'Строк в fit: {len(Xfit)}, в holdout: {len(Xhold)}')

    pool_fit = to_pool(Xfit, feature_cols, Xfit['target_delay_s'].values)
    model = CatBoostRegressor(loss_function='MAE', depth=6, iterations=1000, learning_rate=0.05,
                               l2_leaf_reg=3.0, random_seed=42, verbose=False)
    model.fit(pool_fit)

    pool_hold = to_pool(Xhold, feature_cols)
    pred_hold = model.predict(pool_hold)

    y_hold = Xhold['target_delay_s'].values
    mae_zero = np.mean(np.abs(y_hold))
    mae_base = np.mean(np.abs(y_hold - Xhold['cur_dev_s'].values))
    mae_model = np.mean(np.abs(y_hold - pred_hold))

    print()
    print(f'MAE (predict 0) на НОВЫХ бортах         : {mae_zero:.1f} s')
    print(f'MAE (baseline cur_dev_s) на НОВЫХ бортах: {mae_base:.1f} s')
    print(f'MAE (модель) на НОВЫХ бортах             : {mae_model:.1f} s')
    print()

    if mae_model < mae_base * 0.15:
        print('!!! MAE ПОДОЗРИТЕЛЬНО НИЗКИЙ (<15% от бейзлайна) на бортах, которых модель НИКОГДА не видела.')
        print('    Это похоже на УТЕЧКУ -- ответ где-то протекает в фичи независимо от конкретного борта.')
    elif mae_model > mae_base * 0.85:
        print('!!! MAE близок к бейзлайну/mae_zero -- модель почти не обобщается на новые борта.')
        print('    Похоже на ПЕРЕОБУЧЕНИЕ под конкретные 39 бортов текущего дня.')
    else:
        print('MAE в разумных пределах относительно бейзлайна -- ни явной утечки, ни серьёзного')
        print('переобучения на конкретные борта не видно. Сравни это число с обычным test MAE:')
        print('если они близки -- всё хорошо.')


if __name__ == '__main__':
    main()