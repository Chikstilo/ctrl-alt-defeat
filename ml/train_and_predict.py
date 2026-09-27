"""
Обучение + предсказание. Меняешь ТОЛЬКО DATA_DIR внизу, ничего больше руками
не трогаешь - все пути собираются из него автоматически (было: сломанный
PATHS, который нигде не создавался - отсюда и мучения).

main.py (FeatureEngine, load_traffic, load_schedule, load_labels, CAT_FEATURES)
должен лежать В ТОЙ ЖЕ ПАПКЕ, что и этот файл.
"""
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.model_selection import KFold

from main import load_traffic, load_schedule, load_labels, FeatureEngine, CAT_FEATURES

# ============================================================================
# ЕДИНСТВЕННОЕ, ЧТО НУЖНО ПРАВИТЬ
# ============================================================================
DATA_DIR = "D:/hack1"  # forward slashes - без мороки с \U и прочим unicode escape

PATHS = {
    "train_traffic": f"{DATA_DIR}/train/traffic.csv",
    "train_schedule": f"{DATA_DIR}/train/schedule.csv",
    "labels_train": f"{DATA_DIR}/labels/labels_train.csv",

    "test_traffic": f"{DATA_DIR}/test/traffic.csv",
    "test_schedule": f"{DATA_DIR}/test/schedule.csv",
    "labels_test": f"{DATA_DIR}/labels/labels_test.csv",

    "val_traffic": f"{DATA_DIR}/validate/traffic.csv",
    "val_schedule_plan": f"{DATA_DIR}/validate/schedule_plan.csv",
    "val_points": f"{DATA_DIR}/validate/points.csv",

    "submission_out": "submission.csv",  # сохранится рядом со скриптом
}


def add_point_history_features(df):
    """
    Подстраховка на случай, если own_delay_* (из schedule) где-то просядут:
    тренд cur_dev_s по ПРЕДЫДУЩИМ прогнозным точкам ЭТОГО ЖЕ борта. Источник
    (labels_train/labels_test/points_validate) одинаковый и безопасный во
    ВСЕХ трёх выборках - в отличие от schedule.csv, тут физически не может
    быть утечки план/факт, потому что cur_dev_s уже дан организаторами как
    know-at-T значение.
    """
    d = df[["tr_id", "T", "cur_dev_s"]].copy()
    d = d.sort_values(["tr_id", "T"])
    g = d.groupby("tr_id")
    d["cur_dev_prev1"] = g["cur_dev_s"].shift(1)
    d["cur_dev_trend"] = (d["cur_dev_s"] - d["cur_dev_prev1"]).fillna(0)
    d["cur_dev_avg_last3"] = g["cur_dev_s"].transform(lambda s: s.rolling(3, min_periods=1).mean())
    return d[["cur_dev_trend", "cur_dev_avg_last3"]]


def make_model(iterations=1500):
    return CatBoostRegressor(
        loss_function="MAE",
        depth=6,
        iterations=iterations,
        learning_rate=0.05,
        l2_leaf_reg=1.0,
        random_seed=42,
        verbose=False,
        early_stopping_rounds=100,
    )


def to_pool(df, feature_cols, target=None):
    cat_idx = [feature_cols.index(c) for c in CAT_FEATURES]
    return Pool(df[feature_cols], label=target, cat_features=cat_idx)


def main():
    print("Пути (проверь, что все файлы реально лежат тут):")
    for k, v in PATHS.items():
        print(f"  {k}: {v}")

    # ---------- 1. train ----------
    print("\nЗагрузка train...")
    traffic_tr = load_traffic(PATHS["train_traffic"])
    schedule_tr = load_schedule(PATHS["train_schedule"], has_fact=True)
    stop_geom_tr = {row.tt_action_item_id: (row.lon, row.lat) for row in schedule_tr.itertuples()}
    lt = load_labels(PATHS["labels_train"])
    fe_tr = FeatureEngine(traffic_tr, schedule_tr, stop_geom_tr)
    Xtr = fe_tr.build(lt)
    Xtr["target_delay_s"] = lt["target_delay_s"].values
    Xtr = Xtr.merge(lt[["sample_id"]].join(add_point_history_features(lt)), on="sample_id", how="left")

    # ЖЁСТКАЯ ПРОВЕРКА: доказываем прямо тут, что новые фичи реально в таблице,
    # а не гадаем по именам файлов и кэшу
    new_feature_names = ["n_stops_to_target", "own_delay_std_all", "stop_geo_bucket",
                          "heading_change_5m", "heading_change_10m", "heading_change_15m"]
    print(">>> ПРОВЕРКА НОВЫХ ПРИЗНАКОВ В Xtr:")
    for f in new_feature_names:
        present = f in Xtr.columns
        non_null = Xtr[f].notna().mean() if present else None
        print(f"    {f}: есть в таблице = {present}, доля непустых = {non_null}")
    assert all(f in Xtr.columns for f in new_feature_names), \
        "СТОП: новых признаков нет в Xtr - main.py устарел, замени файл целиком!"

    from sklearn.model_selection import GroupShuffleSplit

    # Внутренняя валидация ИЗ TRAIN, по группам tr_id (не по строкам!) —
    # нужна для early stopping и всех "экспериментальных" решений ниже.
    # Test отныне используется РОВНО ОДИН РАЗ, в самом конце, только для чтения.
    gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    fit_idx, internal_val_idx = next(gss.split(Xtr, groups=Xtr["tr_id_cat"]))
    Xtr_fit = Xtr.iloc[fit_idx].reset_index(drop=True)
    Xtr_ival = Xtr.iloc[internal_val_idx].reset_index(drop=True)
    print(f"Внутренний сплит: fit={len(Xtr_fit)} строк, val={len(Xtr_ival)} строк "
        f"({Xtr_fit['tr_id_cat'].nunique()} vs {Xtr_ival['tr_id_cat'].nunique()} бортов)")

    # ---------- 2. test ----------
    print("Загрузка test...")
    traffic_te = load_traffic(PATHS["test_traffic"])
    schedule_te = load_schedule(PATHS["test_schedule"], has_fact=True)
    stop_geom_te = {row.tt_action_item_id: (row.lon, row.lat) for row in schedule_te.itertuples()}
    le = load_labels(PATHS["labels_test"])
    fe_te = FeatureEngine(traffic_te, schedule_te, stop_geom_te)
    Xte = fe_te.build(le)
    Xte["target_delay_s"] = le["target_delay_s"].values
    Xte = Xte.merge(le[["sample_id"]].join(add_point_history_features(le)), on="sample_id", how="left")

    feature_cols = [c for c in Xtr.columns if c not in ("sample_id", "target_delay_s")]

    # ---------- 3. локальная оценка на test-holdout ----------
    print("\nОбучение и оценка на test...")
    pool_tr = to_pool(Xtr_fit, feature_cols, Xtr_fit["target_delay_s"].values)
    pool_ival = to_pool(Xtr_ival, feature_cols, Xtr_ival["target_delay_s"].values)
    pool_te = to_pool(Xte, feature_cols, Xte["target_delay_s"].values)  # только для чтения в конце

    model = make_model()
    model.fit(pool_tr, eval_set=pool_ival, use_best_model=True)  # ранняя остановка по internal val
    pred_te = model.predict(pool_te)  # test виден модели впервые прямо здесь

    mae_zero = np.mean(np.abs(Xte["target_delay_s"].values))
    mae_base = np.mean(np.abs(Xte["target_delay_s"].values - Xte["cur_dev_s"].values))
    mae_model = np.mean(np.abs(Xte["target_delay_s"].values - pred_te))
    print(f"MAE (predict 0)          : {mae_zero:.1f} s")
    print(f"MAE (baseline cur_dev_s) : {mae_base:.1f} s")
    print(f"MAE (CatBoost, test)     : {mae_model:.1f} s")
    print(f"улучшение над бейзлайном : {(mae_base - mae_model) / mae_base * 100:.1f}%")

# ---------- ablation: нужен ли tr_id_cat, или он рискует переобучением на 39 бортах ----------
    CAT_FEATURES_no_trid = [c for c in CAT_FEATURES if c != "tr_id_cat"]

    def to_pool_custom(df, feature_cols, cat_features, target=None):
        cat_idx = [feature_cols.index(c) for c in cat_features]
        return Pool(df[feature_cols], label=target, cat_features=cat_idx)

    pool_tr_no_trid = to_pool_custom(Xtr_fit, feature_cols, CAT_FEATURES_no_trid, Xtr_fit["target_delay_s"].values)
    pool_ival_no_trid = to_pool_custom(Xtr_ival, feature_cols, CAT_FEATURES_no_trid, Xtr_ival["target_delay_s"].values)

    model_no_trid = make_model()
    model_no_trid.fit(pool_tr_no_trid, eval_set=pool_ival_no_trid, use_best_model=True)

    # Пул для теста — своя схема категориальных фичей, отдельно от общего pool_te
    pool_te_no_trid = to_pool_custom(Xte, feature_cols, CAT_FEATURES_no_trid, Xte["target_delay_s"].values)
    pred_no_trid = model_no_trid.predict(pool_te_no_trid)
    mae_no_trid = np.mean(np.abs(Xte["target_delay_s"].values - pred_no_trid))

    print(f"\nMAE С tr_id_cat  : {mae_model:.1f}")
    print(f"MAE БЕЗ tr_id_cat : {mae_no_trid:.1f}")

    # ---------- 4. 5-fold CV по train ----------
    from sklearn.model_selection import GroupKFold
    import itertools

    def cv_mae(Xtr, feature_cols, cat_features, params, n_splits=5):
        """CV MAE по GroupKFold(tr_id) — честная оценка конфигурации гиперпараметров."""
        gkf = GroupKFold(n_splits=n_splits)
        maes = []
        for tr_idx, va_idx in gkf.split(Xtr, groups=Xtr["tr_id_cat"]):
            p_tr = to_pool_custom(Xtr.iloc[tr_idx], feature_cols, cat_features,
                                Xtr["target_delay_s"].values[tr_idx])
            p_va = to_pool_custom(Xtr.iloc[va_idx], feature_cols, cat_features,
                                Xtr["target_delay_s"].values[va_idx])
            m = CatBoostRegressor(loss_function="MAE", random_seed=42, verbose=False,
                                early_stopping_rounds=100, iterations=2000, **params)
            m.fit(p_tr, eval_set=p_va, use_best_model=True)
            pred = m.predict(p_va)
            maes.append(np.mean(np.abs(Xtr["target_delay_s"].values[va_idx] - pred)))
        return float(np.mean(maes)), float(np.std(maes))


    # ------------------------------------------------------------------
    # OBJECT IMPORTANCE: какие КОНКРЕТНЫЕ строки train помогли/навредили
    # предсказаниям на test. Отрицательная важность = строка мешает модели
    # (шум/выброс/нерепрезентативный пример) - убрав её, ошибка бы упала.
    #
    # Дорого по времени (масштабируется как |train| x |test|), поэтому
    # считаем ОДИН раз на уже обученной модели, не внутри CV-цикла.
    # ------------------------------------------------------------------
    print("\nСчитаю object importance (может занять время)...")
    indices, scores = model.get_object_importance(pool_ival, pool_tr, importance_values_sign="All")
    obj_imp = pd.DataFrame({"train_row_idx": indices, "importance": scores})
    obj_imp["tr_id"] = Xtr_fit["tr_id_cat"].values[obj_imp["train_row_idx"]]
    obj_imp["is_synthetic"] = obj_imp["tr_id"].astype(float) >= 9000000
    print("\nСамые ВРЕДНЫЕ строки train (отрицательная importance - мешают модели):")
    print(obj_imp.sort_values("importance").head(15))

    print("\nДоля синтетики среди топ-200 самых вредных строк:",
          obj_imp.sort_values("importance").head(200)["is_synthetic"].mean())
    print("Доля синтетики во всём train:",
          obj_imp["is_synthetic"].mean())

    # ------------------------------------------------------------------
    # ЭКСПЕРИМЕНТ: реально убираем топ-N вредных строк, переобучаем,
    # сравниваем MAE на том же test - только так можно узнать, помогает
    # это или нет, а не гадать по одной лишь доле синтетики.
    # ------------------------------------------------------------------
    for n_remove in [100, 200, 500]:
        harmful_idx = obj_imp.sort_values("importance").head(n_remove)["train_row_idx"].values
        Xtr_fit_clean = Xtr_fit.drop(index=harmful_idx).reset_index(drop=True)
        pool_tr_clean = to_pool(Xtr_fit_clean, feature_cols, Xtr_fit_clean["target_delay_s"].values)
        model_clean = make_model()
        model_clean.fit(pool_tr_clean, eval_set=pool_ival, use_best_model=True)
        pred_clean = model_clean.predict(pool_te)  # test здесь — зритель, не судья при выборе n_remove
        mae_clean = np.mean(np.abs(Xte["target_delay_s"].values - pred_clean))
        print(f"Убрали {n_remove} вредных строк -> MAE на test: {mae_clean:.1f}"
              f"(было {mae_model:.1f}, разница {mae_model - mae_clean:+.1f})")

    # ---------- 5. финальное обучение на train+test, предсказание validate ----------
    print("\nФинальное обучение на train+test, прогноз validate...")
    Xall = pd.concat([Xtr, Xte], ignore_index=True)
    pool_all = to_pool(Xall, feature_cols, Xall["target_delay_s"].values)
    final_model = make_model(iterations=model.get_best_iteration() or 1000)
    final_model.fit(pool_all)

    traffic_val = load_traffic(PATHS["val_traffic"])
    schedule_plan = load_schedule(PATHS["val_schedule_plan"], has_fact=False)
    stop_geom_val = dict(stop_geom_tr)
    stop_geom_val.update(stop_geom_te)
    stop_geom_val.update({row.tt_action_item_id: (row.lon, row.lat) for row in schedule_plan.itertuples()})

    pv = load_labels(PATHS["val_points"])
    schedule_for_own_history = pd.concat([schedule_tr, schedule_te], ignore_index=True)
    fe_val = FeatureEngine(traffic_val, schedule_for_own_history, stop_geom_val)
    Xval = fe_val.build(pv)
    Xval = Xval.merge(pv[["sample_id"]].join(add_point_history_features(pv)), on="sample_id", how="left")

    pool_val = to_pool(Xval, feature_cols)
    pred = final_model.predict(pool_val)
    sub = pd.DataFrame({"sample_id": Xval["sample_id"], "prediction": pred})
    sub.to_csv(PATHS["submission_out"], sep=";", index=False)
    print(f"\nsubmission.csv готов ({len(sub)} строк): {PATHS['submission_out']}")
        # ---------- 6. Артефакты для backend ----------
    final_model.save_model("model.cbm")

    import json
    with open("model_contract.json", "w", encoding="utf-8") as f:
        json.dump({
            "feature_cols": feature_cols,       # ТОЧНЫЙ порядок — CatBoost чувствителен к порядку колонок
            "cat_features": CAT_FEATURES,
            "test_mae": float(mae_model),        # честный после фикса выше
        }, f, ensure_ascii=False, indent=2)
    print("\nСохранено: model.cbm, model_contract.json")

if __name__ == "__main__":
    main()