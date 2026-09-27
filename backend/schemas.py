from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TelemetryIn(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=50)
    route_id: str | None = Field(default=None, max_length=50)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float | None = None
    speed: float | None = Field(default=None, ge=0, le=300)
    heading: float | None = Field(default=None, ge=0, le=360)
    location_valid: bool | None = None
    stop_id: str | None = Field(default=None, max_length=50)
    dwell_time_seconds: float | None = Field(default=None, ge=0)
    event_time: datetime


class TelemetryOut(TelemetryIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class VehicleRouteIn(BaseModel):
    unit_id: int = Field(ge=0, le=2147483647)
    vehicle_id: str = Field(min_length=1, max_length=50)
    route_id: str = Field(min_length=1, max_length=50)
    active: bool = True


class VehicleRouteOut(VehicleRouteIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class PredictionOut(BaseModel):
    vehicle_id: str
    route_id: str
    delay_seconds: int = Field(ge=-600, le=3600)
    probability: float | None = Field(default=None, ge=0, le=100)
    risk_level: str = Field(pattern=r"^(low|medium|high)$")
    scheduled_arrival: datetime | None = None
    arrival_time: datetime                      # вместо predicted_for
    recommendation: str | None = None
    updated_at: datetime | None = None


class RoutePredictionsOut(BaseModel):
    route_id: str
    count: int
    vehicles: list[PredictionOut]


class ScheduleIn(BaseModel):
    route_id: str = Field(min_length=1, max_length=50)
    stop_id: str = Field(min_length=1, max_length=50)
    stop_name: str | None = Field(default=None, max_length=200)
    scheduled_arrival: datetime
    lat: float | None = None
    lon: float | None = None


class ScheduleOut(ScheduleIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    postgres: bool
    redis: bool
    ml_service: bool = False
    ndtp_tcp: bool = False
    queue_size: int = 0
    cached_routes: int = 0

class WorkerStats(BaseModel):
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    last_update: str | None = None
    queue_size: int = 0

class DispatchAlert(BaseModel):
    route_id: str
    vehicle_id: str
    delay_seconds: int
    probability: float | None = None
    risk_level: str
    scheduled_arrival: datetime | None = None
    arrival_time: datetime
    recommendation: str | None = None


class DispatchOut(BaseModel):
    count: int
    alerts: list[DispatchAlert]