"""
Обучение CatBoost для предсказания target_delay_s.
Запуск: python train_model.py path/to/dataset
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from catboost import CatBoostRegressor


def load_traffic(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["event_time"] = pd.to_datetime(df["event_time"])
    return df


def build_features(traffic: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """
    Для каждой прогнозной точки (sample_id, tr_id, T) собираем фичи:
    только по телеметрии с event_time <= T.
    """
    rows = []
    traffic_by_tr = {tr: g.sort_values("event_time") for tr, g in traffic.groupby("tr_id")}

    for _, lbl in labels.iterrows():
        tr = lbl["tr_id"]
        T = pd.to_datetime(lbl["T"])
        g = traffic_by_tr.get(tr)
        if g is None:
            continue
        past = g[g["event_time"] <= T]
        if past.empty:
            continue
        last = past.iloc[-1]
        # простые фичи
        rows.append({
            "sample_id": lbl["sample_id"],
            "cur_dev_s": lbl["cur_dev_s"],
            "speed": last.get("speed", 0),
            "heading": last.get("heading", 0),
            "hour": T.hour,
            "dow": T.dayofweek,
            "n_points": len(past),
            "speed_mean_10": past["speed"].tail(10).mean(),
            "speed_min_10": past["speed"].tail(10).min(),
            "target": lbl["target_delay_s"],
        })
    return pd.DataFrame(rows)


def main(root: str):
    root = Path(root)
    print("Загрузка…")
    train_traffic = load_traffic(root / "train" / "traffic.csv")
    train_labels = pd.read_csv(root / "labels" / "labels_train.csv")
    test_traffic = load_traffic(root / "test" / "traffic.csv")
    test_labels = pd.read_csv(root / "labels" / "labels_test.csv")

    print("Фичи…")
    Xtr = build_features(train_traffic, train_labels)
    Xte = build_features(test_traffic, test_labels)

    feats = [c for c in Xtr.columns if c not in ("sample_id", "target")]
    print(f"Фичи: {feats}")

    model = CatBoostRegressor(
        iterations=2000, learning_rate=0.05, depth=6,
        loss_function="MAE", verbose=200,
    )
    model.fit(Xtr[feats], Xtr["target"])

    pred = model.predict(Xte[feats])
    mae = (abs(pred - Xte["target"])).mean()
    print(f"MAE на test: {mae:.2f} сек")

    model.save_model("ml_service/models/model.cbm")
    print("✅ Модель сохранена в ml_service/models/model.cbm")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python train_model.py path/to/dataset")
        sys.exit(1)
    main(sys.argv[1])