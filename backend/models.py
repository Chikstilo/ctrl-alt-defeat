from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class VehicleRoute(Base):
    __tablename__ = "vehicle_routes"
    __table_args__ = (
        CheckConstraint("unit_id >= 0 AND unit_id <= 2147483647"),
        Index("ix_vehicle_routes_route_id", "route_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    vehicle_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    route_id: Mapped[str] = mapped_column(String(50), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TelemetryRecord(Base):
    __tablename__ = "telemetry"
    __table_args__ = (
        Index("ix_vehicle_event_time", "vehicle_id", "event_time"),
        Index("ix_route_event_time", "route_id", "event_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(String(50), nullable=False)
    route_id: Mapped[str | None] = mapped_column(String(50))
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    alt: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    location_valid: Mapped[bool | None] = mapped_column(Boolean)
    stop_id: Mapped[str | None] = mapped_column(String(50))
    dwell_time_seconds: Mapped[float | None] = mapped_column(Float)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        Index("ix_prediction_route_created", "route_id", "created_at"),
        Index("ix_prediction_vehicle_created", "vehicle_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[str] = mapped_column(String(50), nullable=False)
    vehicle_id: Mapped[str | None] = mapped_column(String(50))
    delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    probability: Mapped[float | None] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    recommendation: Mapped[str | None] = mapped_column(String(300))
    scheduled_arrival: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    arrival_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedule_route_arrival", "route_id", "scheduled_arrival"),
        UniqueConstraint(
            "route_id", "stop_id", "scheduled_arrival",
            name="uq_schedule_route_stop_arrival",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[str] = mapped_column(String(50), nullable=False)
    stop_id: Mapped[str] = mapped_column(String(50), nullable=False)
    stop_name: Mapped[str | None] = mapped_column(String(200))
    scheduled_arrival: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # НОВЫЕ КОЛОНКИ
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    
class ProcessedStreamMessage(Base):
    __tablename__ = "processed_stream_messages"

    # Например: ndtp:stream/1730000000000-0
    stream_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
class StopCrossing(Base):
    __tablename__ = "stop_crossings"
    __table_args__ = (
        Index("idx_stop_crossings_vehicle", "vehicle_id", "actual_time"),
        Index("idx_stop_crossings_route", "route_id", "actual_time"),
        UniqueConstraint(
            "vehicle_id", "stop_id", "planned_time",
            name="uq_stop_crossing_vehicle_stop_planned",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(String(50), nullable=False)
    route_id: Mapped[str | None] = mapped_column(String(50))
    stop_id: Mapped[str] = mapped_column(String(50), nullable=False)
    planned_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delay_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )