"""Event and dead-letter replay with idempotency and an audit trail."""

import uuid
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ReplayRequest, ReplayResponse
from app.core.auth import ApiKeyIdentity, require_replay
from app.db.session import get_session
from app.models.dead_letter import DeadLetter
from app.models.delivery import Delivery
from app.models.destination import Destination
from app.models.event import Event
from app.models.replay import Replay
from app.services.delivery import delivery_key

router = APIRouter(tags=["replay"])

MAX_REPLAY_DESTINATIONS = 1000


def _idempotency_key(value: str | None) -> str:
    if value is None or not value.strip() or len(value) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key is required (max 255 characters)",
        )
    return value


async def _targets_for_event(
    session: AsyncSession,
    event: Event,
    request: ReplayRequest,
) -> list[Destination]:
    if request.destination_ids is not None:
        wanted = set(request.destination_ids)
        rows = (await session.scalars(select(Destination).where(Destination.id.in_(wanted)))).all()
        if len(rows) != len(wanted):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="unknown destination id",
            )
        if any(destination.source_id != event.source_id for destination in rows):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="destination does not belong to the event source",
            )
        targets = list(rows)
    else:
        targets = list(
            (
                await session.scalars(
                    select(Destination).where(
                        Destination.source_id == event.source_id,
                        Destination.active.is_(True),
                    )
                )
            ).all()
        )

    if request.failed_only:
        delivery_rows = (
            await session.execute(
                select(Delivery.destination_id, Delivery.status).where(
                    Delivery.event_id == event.id
                )
            )
        ).all()
        by_destination: dict[UUID, set[str]] = {}
        for destination_id, delivery_status in delivery_rows:
            by_destination.setdefault(destination_id, set()).add(delivery_status)
        failed = {
            destination_id
            for destination_id, statuses in by_destination.items()
            if "success" not in statuses and ("failed" in statuses or "dead" in statuses)
        }
        targets = [destination for destination in targets if destination.id in failed]

    return sorted(targets, key=lambda destination: destination.id)


async def _create_replay(
    session: AsyncSession,
    *,
    event: Event,
    destinations: list[Destination],
    idempotency_key: str,
    identity: ApiKeyIdentity,
    delivery_id: UUID | None = None,
    dead_letter: DeadLetter | None = None,
) -> ReplayResponse:
    if not destinations:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no destinations to replay",
        )
    if len(destinations) > MAX_REPLAY_DESTINATIONS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"replay supports at most {MAX_REPLAY_DESTINATIONS} destinations",
        )

    destination_ids = sorted(destination.id for destination in destinations)
    locked = (
        await session.scalars(
            select(Destination)
            .where(Destination.id.in_(destination_ids))
            .order_by(Destination.id)
            .with_for_update()
        )
    ).all()
    if len(locked) != len(destination_ids):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a destination disappeared during replay",
        )

    replay_id = (
        await session.execute(
            pg_insert(Replay)
            .values(
                id=uuid.uuid4(),
                event_id=event.id,
                delivery_id=delivery_id,
                api_key_id=identity.id,
                idempotency_key=idempotency_key,
                deliveries_created=0,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Replay.id)
        )
    ).scalar_one_or_none()

    if replay_id is None:
        existing = await session.scalar(
            select(Replay).where(Replay.idempotency_key == idempotency_key)
        )
        if existing is None or existing.event_id != event.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="idempotency key already used for another event",
            )
        return ReplayResponse(
            replay_id=existing.id,
            event_id=existing.event_id,
            deliveries_created=existing.deliveries_created,
        )

    rows = (
        await session.execute(
            select(Delivery.destination_id, func.max(Delivery.attempt))
            .where(
                Delivery.event_id == event.id,
                Delivery.destination_id.in_(destination_ids),
            )
            .group_by(Delivery.destination_id)
        )
    ).all()
    max_attempt = {destination_id: int(highest or 0) for destination_id, highest in rows}

    now = datetime.now(UTC)
    for destination in destinations:
        session.add(
            Delivery(
                event_id=event.id,
                destination_id=destination.id,
                attempt=max_attempt.get(destination.id, 0) + 1,
                status="pending",
                next_attempt_at=now,
                delivery_key=delivery_key(event.id, destination.id),
            )
        )

    event.status = "delivering"
    if dead_letter is not None:
        dead_letter.replayed_at = now

    replay = await session.get(Replay, replay_id)
    if replay is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="replay record disappeared",
        )
    replay.deliveries_created = len(destinations)

    await session.commit()
    return ReplayResponse(
        replay_id=replay.id,
        event_id=event.id,
        deliveries_created=replay.deliveries_created,
    )


@router.post(
    "/events/{event_id}/replay",
    response_model=ReplayResponse,
    summary="Replay an event to all or failed destinations",
)
async def replay_event(
    event_id: UUID,
    payload: ReplayRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[ApiKeyIdentity, Depends(require_replay)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReplayResponse:
    key = _idempotency_key(idempotency_key)

    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="event not found")

    destinations = await _targets_for_event(session, event, payload)
    return await _create_replay(
        session,
        event=event,
        destinations=destinations,
        idempotency_key=key,
        identity=identity,
    )


@router.post(
    "/admin/dead-letters/{dead_letter_id}/replay",
    response_model=ReplayResponse,
    summary="Replay a dead-lettered delivery",
)
async def replay_dead_letter(
    dead_letter_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[ApiKeyIdentity, Depends(require_replay)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReplayResponse:
    key = _idempotency_key(idempotency_key)

    dead_letter = await session.get(DeadLetter, dead_letter_id)
    if dead_letter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dead letter not found")

    delivery = await session.get(Delivery, dead_letter.delivery_id)
    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="original delivery no longer exists",
        )
    event = await session.get(Event, delivery.event_id)
    destination = await session.get(Destination, delivery.destination_id)
    if event is None or destination is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="event or destination no longer exists",
        )

    return await _create_replay(
        session,
        event=event,
        destinations=[destination],
        idempotency_key=key,
        identity=identity,
        delivery_id=delivery.id,
        dead_letter=dead_letter,
    )
