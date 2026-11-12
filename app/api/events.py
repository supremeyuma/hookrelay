"""Read-only event inspection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import DeliveryAttempt, EventDetail
from app.db.session import get_session
from app.models.delivery import Delivery
from app.models.event import Event

router = APIRouter(tags=["events"])


@router.get(
    "/events/{event_id}",
    response_model=EventDetail,
    summary="Get an event and its delivery attempts",
)
async def get_event(
    event_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EventDetail:
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="event not found")

    attempts = (
        await session.scalars(
            select(Delivery)
            .where(Delivery.event_id == event.id)
            .order_by(Delivery.created_at, Delivery.attempt)
        )
    ).all()

    detail = EventDetail.model_validate(event)
    detail.attempts = [DeliveryAttempt.model_validate(attempt) for attempt in attempts]
    return detail
