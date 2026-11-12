"""Read-only event inspection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import EventDetail
from app.db.session import get_session
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
    return EventDetail.model_validate(event)
