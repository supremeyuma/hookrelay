"""Outbox poller process.

Runs every second: expands unpublished events into deliveries, then re-enqueues any
pending delivery that is due. A separate process (rather than an in-API task) keeps the
API stateless and lets the poller share work safely across replicas via SKIP LOCKED.
"""

import asyncio
import logging
from uuid import UUID

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.services.delivery import enqueue_due_deliveries, publish_outbox

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.0


async def _enqueue(pool: ArqRedis, delivery_id: UUID) -> None:
    await pool.enqueue_job(
        "deliver",
        str(delivery_id),
        _job_id=f"delivery:{delivery_id}",
    )


async def run_poller() -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    async def enqueue(delivery_id: UUID) -> None:
        await _enqueue(pool, delivery_id)

    try:
        while True:
            try:
                async with database.session_factory() as session:
                    published = await publish_outbox(session, enqueue)
                    due = await enqueue_due_deliveries(session, enqueue)
                if published or due:
                    logger.info(
                        "poller_tick",
                        extra={"published": published, "due": due},
                    )
            except Exception:
                logger.exception("poller_tick_failed")

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        await pool.close()
        await database.dispose()


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(run_poller())


if __name__ == "__main__":
    main()
