# Interview Notes

## Whiteboard the data model in two minutes

```text
sources(id, slug, secret_encrypted, rate, burst)
  └── events(id, source_id, idempotency_key, payload, status)
        ├── outbox(id, event_id, traceparent, published_at)
        └── deliveries(id, event_id, destination_id, attempt,
                       status, response_code, error_class,
                       next_attempt_at, delivery_key, traceparent)
              └── dead_letters(id, delivery_id, reason, replayed_at)

destinations(id, source_id, url, secret_encrypted, active)
api_keys(id, key_hash, key_prefix, scopes[], active, last_used_at)
replays(id, event_id, delivery_id, api_key_id, idempotency_key, deliveries_created)
```

The two invariants to say out loud:

1. `UNIQUE(source_id, idempotency_key)` makes accept idempotent.
2. Every delivery attempt shares a stable `delivery_key`, so consumers can dedupe an
   at-least-once system.

## Three things that break first and how I detect them

1. **API CPU / worker count.** One Python worker tops out well below the load target; the
   signal is rising `hookrelay_ingest_duration_seconds` p95 with CPU pinned before error rate
   moves. Response: add workers, then reduce per-request work. At 10×, size Postgres pools
   globally, not per worker.
2. **Postgres commit throughput.** Event + outbox insert + commit is the dominant per-request
   cost. Watch ingest p95 and Postgres disk wait together. Response: faster disk, PgBouncer,
   partitioning, then a log-based pipeline if needed.
3. **Outbox lag.** `hookrelay_outbox_lag_seconds` growing means the poller is behind. Response:
   more pollers (`SKIP LOCKED` makes them safe), larger batches, and an index on unpublished
   rows. This actually happened at 400 rps before the N+1 fix.

## One bug, debugged

A signed ingest smoke test returned 401 while the unit tests passed. The signature helper was
correct; the shell was the problem. PowerShell 5.1 strips embedded double quotes when passing
a JSON string to a native executable, so the body that was signed was not the body that was
sent. I reproduced it by printing the argument and the HMAC input, then replaced the shell
flow with a Python smoke script that owns the exact bytes. Commit:
`chore(scripts): signing helper and end-to-end ingest smoke check`.

## Questions to expect

- **"Why not exactly once?"** Because the HTTP call and the database commit are two systems;
  the honest contract is at-least-once plus an idempotency key. I say that in the README.
- **"Why an outbox instead of publishing directly?"** A commit-then-publish crash loses work.
  The outbox makes the durable record the source of truth and lets a poller recover.
- **"Why retry 5xx but not 400?"** A 400 is deterministic for that payload; retrying it
  amplifies load on a broken subscriber. 408/425/429 are transient by contract.
- **"How did you test concurrency?"** 100 identical signed requests across threads → one
  event; concurrent replays with the same key → one attempt set; distinct keys serialize
  through destination row locks.
- **"What would you change at 10×?"** More workers, PgBouncer, partitioned tables, multiple
  pollers/workers, circuit breakers, Redis HA, and tracing sampled rather than always-on.
