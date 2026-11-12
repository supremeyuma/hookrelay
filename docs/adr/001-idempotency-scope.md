# ADR 001: Per-source idempotency scope

- **Status:** Accepted
- **Date:** 2026-11-12
- **Deciders:** HookRelay maintainer

## Context

Providers retry webhooks. A retry can arrive seconds or hours later, from a different
connection, and sometimes concurrently. We must store exactly one event per logical
provider event without requiring the provider to change its payload.

The idempotency key arrives in `X-Idempotency-Key`. Different providers can legitimately
choose the same key (`evt_1`, `12345`), and one provider may use the same key for different
event types if it is careless. The scope of uniqueness decides whether one tenant can
suppress another tenant's event.

## Options considered

1. **Global unique key** — `UNIQUE(idempotency_key)` across all sources. Simple, but one
   provider's key namespace can collide with another's and silently drop a valid event.
2. **Per-source composite key** — `UNIQUE(source_id, idempotency_key)`. Keys are scoped to
   the tenant that supplied them. The API looks up the source before the insert, so the
   composite is cheap to enforce.
3. **Per-destination key** — dedupe at fan-out time. This protects subscribers but still
   stores duplicate events and makes the event log lie about what was received.

## Decision

Option 2: `UNIQUE(source_id, idempotency_key)`, enforced in Postgres and handled with
`INSERT ... ON CONFLICT DO NOTHING RETURNING id`. A missing key is a 422; keys are limited
to 255 characters. The source secret is verified before the key is trusted, so a caller
cannot poison another source's key space.

A retry returns `200 {"status":"duplicate"}` with the original event id, while a first
accept returns `202 {"status":"accepted"}`. Both include a `Location` header.

## Consequences

- **Positive:** no cross-tenant suppression; the dedupe decision is a single indexed insert;
  concurrency is handled by Postgres rather than application locks.
- **Negative / accepted cost:** clients must send a stable key per source; changing the key
  on retry creates a duplicate. The API cannot dedupe across two sources that forward the
  same upstream event — that is intentional and documented.
- **What would make us revisit:** a provider that cannot supply a stable key. At that point
  add a fallback key derived from a canonical body hash, and record which strategy was used
  on the event row.

## Links

- `app/api/ingest.py`
- `alembic/versions/0002_sources_events_outbox.py`
- `tests/integration/test_ingest.py::test_100_concurrent_duplicates_create_one_event`
