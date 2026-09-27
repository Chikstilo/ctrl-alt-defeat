import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from model import ModelWrapper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ml_service")

model = ModelWrapper()


@asynccontextmanager
async def lifespan(app: FastAPI):
    model.load()
    logger.info("ML service ready, loaded=%s", model.loaded)
    yield


app = FastAPI(
    title="MoscTrans ML Service",
    version="2.0.0",
    lifespan=lifespan,
)


class PredictRequest(BaseModel):
    """
    Словарь из 45 фич, которые воркер построил сам.
    Имена должны совпадать с feature_cols из model_contract.json.
    """
    features: dict[str, float | int | str | None]


class PredictResponse(BaseModel):
    delay_seconds: int = Field(ge=-600, le=3600)
    probability: float = Field(ge=0, le=100)
    risk_level: str = Field(pattern=r"^(low|medium|high)$")
    scheduled_arrival: datetime
    arrival_time: datetime
    recommendation: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_loaded": model.loaded}


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest) -> PredictResponse:
    if not model.loaded:
        raise HTTPException(status_code=503, detail="Модель не загружена.")
    return PredictResponse(**model.predict(request.features))