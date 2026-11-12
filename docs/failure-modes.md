# Failure Modes and Capacity Notes

Numbers come from the reference run in `load/results/ingest-2026-11-12.txt`:
**419 ingest req/s, p95 31.8 ms, 0% errors**, 4 uvicorn workers, 10 k6 VUs, on an
i7-14650HX (24 logical CPUs, 15.4 GiB to Docker), Windows 11 + WSL2.

## What breaks first at 10× (≈4,200 req/s)

| # | Failure | Why it happens | Signal | First response |
|---|---|---|---|---|
| 1 | API CPU saturation | Each worker is a single Python process. The reference host needed 4 workers for ~420 rps; 10× needs roughly 40 workers or more efficient request handling. | `http_req_duration` p95 climbs before errors; CPU pinned per worker; uvicorn accepts but queues | Scale workers, then reduce per-request work (HMAC/JSON/DB round trips), then move ingest to a faster runtime if needed. |
| 2 | Postgres connection exhaustion | Each worker has `DB_POOL_SIZE + DB_MAX_OVERFLOW` (5+5). Four workers use up to 40; 40 workers would try 400 against `max_connections=100`. | `TimeoutError: connection pool exhausted`, 500s, `pg_stat_activity` near max | Add PgBouncer, lower pool sizes, raise `max_connections` deliberately. Pool sizing is per worker, not global. |
| 3 | Commit latency / disk fsync | Every accepted event is an event insert + outbox insert + commit. On a laptop Docker volume this is the dominant cost; on network storage it gets worse. | Ingest p95 and Postgres commit latency rise together; `pg_stat_bgwriter` / disk wait | Faster disk, `synchronous_commit` tradeoff only with an explicit durability decision, batch/group commits, partition large tables. |
| 4 | Outbox lag | The poller expands `BATCH=500` rows per second per process. At 4.2k rps one poller is far behind. | `hookrelay_outbox_lag_seconds` grows monotonically; events stay `pending` | Run multiple pollers (`FOR UPDATE SKIP LOCKED` makes this safe), increase batch, and index the unpublished partial index. |
| 5 | Delivery worker throughput | One ARQ worker processes `MAX_JOBS=20`. Slow subscribers back up the queue. | Pending deliveries and queue depth grow; `hookrelay_delivery_attempts_total` rate drops | Scale workers (`docker compose up --scale worker=N`), isolate slow destinations, add circuit breakers (in "what I'd do next"). |
| 6 | Retry storm / DLQ growth | A subscriber returning 500 for an hour creates retries and then dead letters. | `hookrelay_dlq_total` rate, delivery error classes | Cap attempts (already 8), add per-destination circuit breaker, alert on DLQ rate. |
| 7 | Redis outage | The token bucket is on the ingest hot path and the queue is Redis-backed. Ingest currently fails closed with a 500 if Redis is unreachable; readiness reports 503. | `/readyz` red, ingest 500s, ARQ stops | Decide explicitly: fail closed (current) or serve a local fallback bucket and degrade rate limiting. Redis Sentinel/Cluster for HA. |
| 8 | Tracing backend down | OTLP exporter is batch/async; a down collector drops spans after the queue fills. | Missing traces, no ingest errors | Keep the exporter optional; never make tracing a request dependency (current behaviour). |
| 9 | Metrics cardinality | Per-path labels are bounded; do not add event ids or source slugs as labels. | Prometheus memory growth | Keep label sets fixed; use logs/traces for high-cardinality data. |

## Detection map

| Signal | Where | Alert threshold (suggested) |
|---|---|---|
| Ingest p95 | `hookrelay_ingest_duration_seconds` (API `/metrics`) | >150 ms for 5 min |
| Ingest failure share | `hookrelay_ingest_total{result="rejected"}` | >1% for 5 min |
| Outbox age | `hookrelay_outbox_lag_seconds` | >30 s for 2 min |
| Delivery results | `hookrelay_delivery_attempts_total` (worker `:9100`) | retry share >20% for 10 min |
| DLQ rate | `hookrelay_dlq_total` (worker `:9100`) | any sustained increase |
| Poller health | `hookrelay_poller_errors_total` (poller `:9101`) | >0 for 5 min |
| Readiness | `/readyz` | 503 for 1 min |

`/metrics` on the API aggregates all uvicorn workers through
`PROMETHEUS_MULTIPROC_DIR`. Worker and poller expose their own registries on 9100/9101;
scrape all three.

## Capacity math

- 419 rps ≈ 36M events/day at the observed p95.
- 10× ingest needs ~10–40 API workers depending on CPU. The poller and worker scale
  horizontally without coordination because claims use `SKIP LOCKED` and ARQ job ids are
  deterministic.
- Postgres writes are the hard wall. Budget for ~5k simple inserts/s on a tuned instance,
  less on laptop storage. Beyond that, partition `events`, move payloads to object storage,
  and consider a log-based pipeline.
- The delivery path is intentionally at-least-once. At 10×, subscriber backpressure, not
  the API, becomes the dominant failure; that is why retries are classified and bounded.

## What I would change at 10×

1. Run more API workers behind a real load balancer, with per-worker pools sized to a global
   Postgres connection budget.
2. Add PgBouncer in transaction mode and partition `events`/`deliveries` by month.
3. Run 2–4 pollers and 4–8 workers; scale on outbox lag and queue depth, not CPU.
4. Add a per-destination circuit breaker and a DLQ alert.
5. Put Redis behind Sentinel or Cluster; decide explicitly between failing closed and a
   degraded local limiter.
6. Keep tracing optional and sampled; never let the collector sit on the request path.
