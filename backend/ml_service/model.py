import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

logger = logging.getLogger("ml_service.model")

MSK_TZ = timezone(timedelta(hours=3))
MODELS_DIR = Path("/app/models")

DEBUG_LOG_MODEL = os.getenv("DEBUG_LOG_MODEL", "0") == "1"


class ModelWrapper:
    def __init__(self) -> None:
        self.loaded = False
        self.model = None
        self.feature_cols: list[str] = []
        self.cat_features: list[str] = []

    def load(self) -> None:
        try:
            model_path = MODELS_DIR / "model.cbm"
            contract_path = MODELS_DIR / "model_contract.json"

            if not model_path.exists():
                logger.warning("model.cbm не найден — fallback")
                self.loaded = False
                return

            self.model = CatBoostRegressor()
            self.model.load_model(str(model_path))

            with open(contract_path, encoding="utf-8") as f:
                contract = json.load(f)
            self.feature_cols = contract["feature_cols"]
            self.cat_features = contract.get("cat_features", [])

            self.loaded = True
            logger.info(
                "Модель загружена: %d фич, MAE=%s",
                len(self.feature_cols),
                contract.get("test_mae"),
            )
        except Exception:
            logger.exception("Ошибка загрузки модели")
            self.loaded = False

    def predict(self, features: dict) -> dict:
        row = {}
        for col in self.feature_cols:
            v = features.get(col)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                row[col] = np.nan
            else:
                row[col] = v

        df = pd.DataFrame([row], columns=self.feature_cols)
        for cat_col in self.cat_features:
            if cat_col in df.columns:
                df[cat_col] = df[cat_col].astype(str)
        if DEBUG_LOG_MODEL:
            try:
                logger.info(
                    "MODEL INPUT columns=%s first_row=%s",
                    list(df.columns),
                    df.iloc[0].to_dict(),
                )
            except Exception:
                logger.exception("Не удалось залогировать вход модели")

        delay_seconds = float(self.model.predict(df)[0])

        if DEBUG_LOG_MODEL:
            try:
                logger.info("MODEL OUTPUT delay_seconds=%.2f", delay_seconds)
            except Exception:
                pass

        delay_seconds = max(-600, min(3600, int(round(delay_seconds))))

        if delay_seconds > 120:
            risk, prob = "high", 85.0
            recommendation = "Выпустить дополнительный ТС на линию"
        elif delay_seconds > 60:
            risk, prob = "medium", 60.0
            recommendation = "Сократить время стоянки на остановках"
        else:
            risk, prob = "low", 20.0
            recommendation = "Задержка в пределах нормы"

        now_msk = datetime.now(MSK_TZ)
        arrival_time = now_msk + timedelta(seconds=delay_seconds)
        scheduled_arrival = arrival_time - timedelta(seconds=delay_seconds)

        return {
            "delay_seconds": delay_seconds,
            "probability": prob,
            "risk_level": risk,
            "arrival_time": arrival_time.isoformat(),
            "scheduled_arrival": scheduled_arrival.isoformat(),
            "recommendation": recommendation,
        }