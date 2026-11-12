"""Delivery attempts: one row per attempt, grouped by a stable delivery key."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DELIVERY_STATUSES = ("pending", "success", "failed", "dead")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    traceparent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "event_id", "destination_id", "attempt", name="uq_deliveries_event_dest_attempt"
        ),
        CheckConstraint(
            "status IN ('pending','success','failed','dead')",
            name="ck_deliveries_status",
        ),
        Index("ix_deliveries_event_id", "event_id"),
        Index(
            "ix_deliveries_pending",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )
