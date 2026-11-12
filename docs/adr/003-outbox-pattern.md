# ADR 003: Transactional outbox with a polling publisher

- **Status:** Accepted
- **Date:** 2026-11-12
- **Deciders:** HookRelay maintainer

## Context

An accepted event must reach every active destination, but writing to Postgres and
publishing to Redis are two systems. A naive dual write can commit the event and then fail
to publish, or publish and then fail to commit. Either case silently loses or fabricates
work. The event store is the source of truth; fan-out is derived state.

The delivery target can be slow or down for an hour. The ingest path must not wait for it.

## Options considered

1. **Publish on write** — after the event commit, enqueue jobs. Simple, but a crash between
   commit and enqueue loses the event permanently, and the window is not theoretical under
   load.
2. **Transactional outbox + polling publisher** — write an `outbox` row in the same
   transaction as the event. A separate process claims unpublished rows with
   `FOR UPDATE SKIP LOCKED`, expands them into per-destination `deliveries`, and enqueues
   ARQ jobs. At-least-once, recoverable, and observable.
3. **CDC (Debezium/logical replication)** — strongest delivery guarantee with a mature
   pipeline, but adds Kafka/Connect and operational surface far beyond this project.

## Decision

Option 2, with these specifics:

- One outbox row per accepted event, written in the ingest transaction (H2).
- A dedicated poller process, ticking every second, not an in-API background task, so the
  API stays stateless and multiple pollers can share work via `SKIP LOCKED`.
- Each outbox row is marked `published_at` only after deliveries exist and are committed.
- If enqueueing fails after the commit, the delivery stays `pending`; the poller
  re-enqueues due deliveries on the next tick. Deterministic ARQ job ids
  (`delivery:<id>`) make re-enqueue idempotent.
- Every attempt is its own `deliveries` row with a stable `delivery_key` shared across
  retries. Consumers can dedupe; the worker never claims exactly-once.
- Retries are scheduled by setting `next_attempt_at` on a new pending row. The poller, not
  the worker, decides when a retry is due.

## Consequences

- **Positive:** an accepted event cannot be lost by a crash between commit and enqueue;
  fan-out is retriable and inspectable; scaling pollers/workers is horizontal;
  `SKIP LOCKED` prevents duplicate expansion.
- **Negative / accepted cost:** up to ~1s of scheduling latency before the first attempt;
  at-least-once means a crashed worker after a successful HTTP call can repeat it — the
  stable delivery key is the consumer contract; the poller is another process to run and
  monitor.
- **What would make us revisit:** sustained ingest above ~1k events/s, or a sub-second
  fan-out SLA. Then replace polling with `LISTEN/NOTIFY` for wake-ups while keeping the
  outbox as the durable record, or move to CDC.

## Links

- `app/services/delivery.py` (`publish_outbox`, `enqueue_due_deliveries`)
- `app/workers/poller.py`
- `docker-compose.yml` (`worker`, `poller` services)
- `tests/integration/test_delivery.py`
