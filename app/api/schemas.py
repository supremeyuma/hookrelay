"""Request and response models for the HTTP API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"


class SourceCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=64, pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    rate_limit_per_second: int = Field(default=50, ge=1, le=10_000)
    burst: int = Field(default=100, ge=1, le=100_000)


class SourceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    rate_limit_per_second: int
    burst: int
    created_at: datetime


class SourceCreated(SourceSummary):
    secret: str = Field(description="Plaintext signing secret. Returned only once.")


class SourceSecret(BaseModel):
    slug: str
    secret: str


class DestinationCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    secret: str | None = Field(default=None, min_length=16, max_length=256)
    active: bool = True


class DestinationUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    active: bool | None = None


class DestinationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    url: str
    active: bool
    created_at: datetime


class DestinationCreated(DestinationSummary):
    secret: str = Field(description="Plaintext delivery secret. Returned only once.")


class IngestResponse(BaseModel):
    status: Literal["accepted", "duplicate"]
    event_id: UUID


class DeliveryAttempt(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    destination_id: UUID
    attempt: int
    status: str
    response_code: int | None = None
    error_class: str | None = None
    next_attempt_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class EventDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    external_id: str | None = None
    idempotency_key: str
    payload: dict[str, Any]
    headers: dict[str, str]
    status: str
    received_at: datetime
    attempts: list[DeliveryAttempt] = Field(default_factory=list)
