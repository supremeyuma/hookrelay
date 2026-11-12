"""Outbox expansion and delivery attempts."""

import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from opentelemetry import trace
from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SecretCipher
from app.core.metrics import DELIVERY_ATTEMPTS, DELIVERY_DURATION, DLQ_TOTAL
from app.core.security import compute_signature
from app.models.dead_letter import DeadLetter
from app.models.delivery import Delivery
from app.models.destination import Destination
from app.models.event import Event
from app.models.outbox import Outbox
from app.services.retry import (
    Outcome,
    classify_status,
    compute_delay,
    error_class_for_status,
    parse_retry_after,
)

logger = logging.getLogger(__name__)

DELIVERY_KEY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "hookrelay.local")
RESPONSE_BODY_LIMIT = 4096

Enqueue = Callable[[uuid.UUID], Awaitable[None]]


@dataclass(frozen=True)
class DeliveryAttemptResult:
    delivery_id: uuid.UUID
    event_id: uuid.UUID
    destination_id: uuid.UUID
    attempt: int
    status: str
    response_code: int | None
    error_class: str | None
    next_attempt_at: datetime | None


def delivery_key(event_id: uuid.UUID, destination_id: uuid.UUID) -> uuid.UUID:
    """Stable key shared by every attempt of one event/destination pair."""
    return uuid.uuid5(DELIVERY_KEY_NAMESPACE, f"{event_id}:{destination_id}")


def canonical_body(payload: dict[str, Any]) -> bytes:
    """Deterministic JSON so the signature covers stable bytes."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def delivery_headers(
    *,
    event_id: uuid.UUID,
    delivery: Delivery,
    secret: str,
    body: bytes,
    now: datetime,
    traceparent: str | None = None,
) -> dict[str, str]:
    timestamp = int(now.timestamp())
    signature = compute_signature(secret, timestamp, body)
    headers = {
        "Content-Type": "application/json",
        "X-HookRelay-Event-Id": str(event_id),
        "X-HookRelay-Delivery-Key": str(delivery.delivery_key),
        "X-HookRelay-Attempt": str(delivery.attempt),
        "X-HookRelay-Timestamp": str(timestamp),
        "X-HookRelay-Signature": f"t={timestamp},v1={signature}",
    }
    if traceparent is not None:
        headers["traceparent"] = traceparent
    return headers


async def refresh_event_status(session: AsyncSession, event: Event) -> str:
    """Recompute the event status from all attempts and persist it on the object."""
    rows = (
        await session.execute(
            select(Delivery.destination_id, Delivery.status).where(Delivery.event_id == event.id)
        )
    ).all()

    by_destination: dict[uuid.UUID, set[str]] = {}
    for destination_id, delivery_status in rows:
        by_destination.setdefault(destination_id, set()).add(delivery_status)

    if not by_destination:
        new_status = "pending"
    elif any("pending" in statuses for statuses in by_destination.values()):
        new_status = "delivering"
    elif all("success" in statuses for statuses in by_destination.values()):
        new_status = "delivered"
    elif any("success" in statuses for statuses in by_destination.values()):
        new_status = "partial"
    else:
        new_status = "dead"

    event.status = new_status
    return new_status


async def deliver_once(
    session: AsyncSession,
    client: httpx.AsyncClient,
    delivery_id: uuid.UUID,
    cipher: SecretCipher,
    *,
    max_attempts: int = 8,
    now: datetime | None = None,
    rng: Callable[[], float] = random.random,
) -> DeliveryAttemptResult | None:
    """Perform one HTTP attempt and persist its outcome.

    Returns ``None`` when the delivery is missing or no longer pending (idempotent).
    Retries are scheduled as a new ``pending`` row; the worker/poller picks it up when due.
    """
    current_time = now or datetime.now(UTC)
    delivery = await session.get(Delivery, delivery_id, with_for_update=True)
    if delivery is None or delivery.status != "pending":
        return None

    event = await session.get(Event, delivery.event_id)
    destination = await session.get(Destination, delivery.destination_id)
    if event is None or destination is None:
        delivery.status = "dead"
        delivery.error_class = "missing_reference"
        delivery.completed_at = current_time
        await session.flush()
        await session.commit()
        return DeliveryAttemptResult(
            delivery_id=delivery.id,
            event_id=delivery.event_id,
            destination_id=delivery.destination_id,
            attempt=delivery.attempt,
            status=delivery.status,
            response_code=None,
            error_class=delivery.error_class,
            next_attempt_at=None,
        )

    body = canonical_body(event.payload)
    secret = cipher.decrypt(destination.secret_encrypted)

    parent_carrier: dict[str, str] = {}
    if delivery.traceparent is not None:
        parent_carrier["traceparent"] = delivery.traceparent
    parent_context = TraceContextTextMapPropagator().extract(parent_carrier)

    started = time.perf_counter()
    response: httpx.Response | None = None
    response_code: int | None = None
    response_text: str | None = None
    error_class: str | None = None
    outcome = Outcome.RETRYABLE

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "hookrelay.deliver",
        context=parent_context,
        kind=SpanKind.PRODUCER,
        attributes={
            "hookrelay.event_id": str(event.id),
            "hookrelay.destination_id": str(destination.id),
            "hookrelay.attempt": delivery.attempt,
        },
    ) as span:
        carrier: dict[str, str] = {}
        inject(carrier)
        headers = delivery_headers(
            event_id=event.id,
            delivery=delivery,
            secret=secret,
            body=body,
            now=current_time,
            traceparent=carrier.get("traceparent"),
        )

        try:
            response = await client.post(destination.url, content=body, headers=headers)
        except httpx.TimeoutException:
            error_class = "timeout"
        except httpx.TransportError:
            error_class = "connection_error"
        except httpx.HTTPError:
            error_class = "request_error"
        else:
            response_code = response.status_code
            response_text = response.content.decode("utf-8", errors="replace")[:RESPONSE_BODY_LIMIT]
            outcome = classify_status(response_code)
            if outcome is not Outcome.SUCCESS:
                error_class = error_class_for_status(response_code)
            span.set_attribute("http.response.status_code", response_code)

        if error_class is not None:
            span.set_attribute("hookrelay.error_class", error_class)

    DELIVERY_DURATION.observe(time.perf_counter() - started)

    delivery.response_code = response_code
    delivery.response_body = response_text
    delivery.completed_at = current_time
    delivery.error_class = error_class

    next_attempt_at: datetime | None = None

    if outcome is Outcome.SUCCESS:
        delivery.status = "success"
        delivery.next_attempt_at = None
    elif outcome is Outcome.PERMANENT:
        delivery.status = "failed"
        delivery.next_attempt_at = None
    elif delivery.attempt >= max_attempts:
        delivery.status = "dead"
        delivery.next_attempt_at = None
        session.add(
            DeadLetter(
                delivery_id=delivery.id,
                reason=error_class or "retry_exhausted",
            )
        )
        DLQ_TOTAL.inc()
    else:
        delivery.status = "failed"
        retry_after = (
            parse_retry_after(response.headers.get("Retry-After"), current_time)
            if response is not None
            else None
        )
        delay = compute_delay(
            delivery.attempt,
            retry_after=retry_after,
            rng=rng,
        )
        next_attempt_at = current_time + timedelta(seconds=delay)
        session.add(
            Delivery(
                event_id=delivery.event_id,
                destination_id=delivery.destination_id,
                attempt=delivery.attempt + 1,
                status="pending",
                next_attempt_at=next_attempt_at,
                delivery_key=delivery.delivery_key,
                traceparent=delivery.traceparent,
            )
        )

    if outcome is Outcome.SUCCESS:
        metric_result = "success"
    elif delivery.status == "dead":
        metric_result = "dead"
    elif outcome is Outcome.PERMANENT:
        metric_result = "permanent"
    else:
        metric_result = "retry"
    DELIVERY_ATTEMPTS.labels(result=metric_result).inc()

    await session.flush()
    await refresh_event_status(session, event)
    await session.commit()

    return DeliveryAttemptResult(
        delivery_id=delivery.id,
        event_id=delivery.event_id,
        destination_id=delivery.destination_id,
        attempt=delivery.attempt,
        status=delivery.status,
        response_code=response_code,
        error_class=error_class,
        next_attempt_at=next_attempt_at,
    )


async def publish_outbox(
    session: AsyncSession,
    enqueue: Enqueue,
    *,
    batch_size: int = 500,
    now: datetime | None = None,
) -> int:
    """Expand unpublished outbox rows into pending deliveries and enqueue them."""
    current_time = now or datetime.now(UTC)
    created: list[uuid.UUID] = []

    try:
        rows = (
            await session.execute(
                select(Outbox, Event)
                .join(Event, Outbox.event_id == Event.id)
                .where(Outbox.published_at.is_(None))
                .order_by(Outbox.created_at, Outbox.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True, of=Outbox)
            )
        ).all()
        if not rows:
            await session.commit()
            return 0

        event_ids = list({event.id for _, event in rows})
        source_ids = list({event.source_id for _, event in rows})

        destinations_by_source: dict[uuid.UUID, list[Destination]] = {}
        for destination in (
            await session.scalars(
                select(Destination)
                .where(
                    Destination.source_id.in_(source_ids),
                    Destination.active.is_(True),
                )
                .order_by(Destination.created_at)
            )
        ).all():
            destinations_by_source.setdefault(destination.source_id, []).append(destination)

        existing = {
            (event_id, destination_id)
            for event_id, destination_id in (
                await session.execute(
                    select(Delivery.event_id, Delivery.destination_id).where(
                        Delivery.event_id.in_(event_ids),
                        Delivery.attempt == 1,
                    )
                )
            ).all()
        }

        for outbox, event in rows:
            outbox.published_at = current_time
            destinations = destinations_by_source.get(event.source_id, [])
            if not destinations:
                continue

            event.status = "delivering"
            for destination in destinations:
                if (event.id, destination.id) in existing:
                    continue

                delivery = Delivery(
                    id=uuid.uuid4(),
                    event_id=event.id,
                    destination_id=destination.id,
                    attempt=1,
                    status="pending",
                    next_attempt_at=current_time,
                    delivery_key=delivery_key(event.id, destination.id),
                    traceparent=outbox.traceparent,
                )
                session.add(delivery)
                created.append(delivery.id)
                existing.add((event.id, destination.id))

        if created:
            await session.flush()
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    for delivery_id in created:
        try:
            await enqueue(delivery_id)
        except Exception:
            logger.exception(
                "delivery_enqueue_failed",
                extra={"delivery_id": str(delivery_id)},
            )

    return len(created)


async def enqueue_due_deliveries(
    session: AsyncSession,
    enqueue: Enqueue,
    *,
    batch_size: int = 500,
    now: datetime | None = None,
) -> int:
    """Re-enqueue pending deliveries whose next attempt is due.

    Deterministic job ids make this idempotent while a job is running or its result is kept.
    """
    current_time = now or datetime.now(UTC)
    due_ids = (
        await session.scalars(
            select(Delivery.id)
            .where(
                Delivery.status == "pending",
                Delivery.next_attempt_at.is_not(None),
                Delivery.next_attempt_at <= current_time,
            )
            .order_by(Delivery.next_attempt_at, Delivery.created_at)
            .limit(batch_size)
        )
    ).all()

    enqueued = 0
    for delivery_id in due_ids:
        try:
            await enqueue(delivery_id)
        except Exception:
            logger.exception(
                "delivery_re_enqueue_failed",
                extra={"delivery_id": str(delivery_id)},
            )
            continue
        enqueued += 1

    return enqueued
