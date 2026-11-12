# HookRelay Roadmap and Gate Evidence

Source plan: `ai-engineering-pivot/execution/01-hookrelay-plan.md`.
Update this file at every phase gate. A checked box means verified, not "written".

## H0 — Bootstrap

- [x] Folder created at `../hookrelay`
- [x] Git repository on `main`, `core.autocrlf=false`
- [x] MIT license, `.gitignore`, `.gitattributes`, `.editorconfig`
- [x] uv project with Python 3.12, runtime and dev dependencies locked
- [x] `app/` package skeleton and `tests/` tree
- [x] `scripts/verify.ps1`
- [x] Package smoke test
- [x] Initial commit
- [x] Private GitHub remote created and pushed

**Evidence**

```
$ uv run pytest
2 passed

$ uv run ruff check .
All checks passed!

$ uv run mypy app
Success: no issues found in 7 source files

$ ./scripts/verify.ps1
ALL CHECKS PASSED
```

Remote: <https://github.com/supremeyuma/hookrelay> (private). Initial push: 3 commits
(`435c821`, `f6002f1`, `58874d6`) on `main`.

## H1 — Skeleton, config, health, compose, CI (M0)

- [x] Settings, structured logging, request IDs
- [x] DB and Redis clients, lifespan
- [x] `/healthz`, `/readyz`
- [x] Compose (api, postgres, redis) + multi-stage Dockerfile
- [x] Alembic wired, baseline revision
- [x] CI: ruff, mypy, pytest, docker build

**Gate evidence**

```
$ docker compose ps
hookrelay-api-1        Up (healthy)   0.0.0.0:8000->8000/tcp
hookrelay-postgres-1   Up (healthy)   0.0.0.0:5432->5432/tcp
hookrelay-redis-1      Up (healthy)   0.0.0.0:6379->6379/tcp

GET /healthz  200 {"status":"ok"}
GET /readyz   200 {"status":"ok","db":"ok","redis":"ok"}
GET /healthz  X-Request-ID: cfae0b2c-aeec-4271-902a-ae12f9fc576c

Redis stopped  -> GET /readyz 503
Redis started  -> GET /readyz 200

$ uv run alembic upgrade head
Running upgrade  -> 0001, baseline
$ uv run alembic current
0001 (head)

$ uv run pytest --cov=app
18 passed; coverage 86.71%

$ uv run ruff check . && uv run mypy app
All checks passed! / Success: no issues found in 16 source files
```

CI run: <https://github.com/supremeyuma/hookrelay/actions/runs/43108810431> — green in 56s
(all steps, including the Docker build). Compose reported the API healthy about 8s after the
image build; Postgres and Redis were healthy before it started.

Example structured log line:

```json
{"ts":"2026-11-11T22:04:43.013308+00:00","level":"INFO","logger":"app.core.middleware","msg":"request_completed","request_id":"d72a36a1-f83e-49c2-b05b-d0761ca6f9bb","method":"GET","path":"/healthz","status_code":200,"duration_ms":0.22}
```


## H2 — Ingest, HMAC, idempotency (M1)

- [x] Sources/events models + migration (outbox table included, used in H3)
- [x] HMAC verification with timestamp tolerance
- [x] Body cap, validation, per-source rate limit
- [x] `ON CONFLICT DO NOTHING` dedupe
- [x] Concurrency test: 100 duplicates → one event
- [x] ADR-001, ADR-002

**Gate evidence**

```
$ uv run alembic upgrade head
Running upgrade 0001 -> 0002, sources, events, and outbox

$ uv run pytest --cov=app
55 passed; coverage 86.62%

$ uv run python scripts/smoke_ingest.py
source: smoke-1794476845 created
first:  202 {'status': 'accepted', 'event_id': '3d89420d-...'}
second: 200 {'status': 'duplicate', 'event_id': '3d89420d-...'}
event:  status=pending amount=4200 signature_stored=False
stale:  401 {'detail': 'invalid signature'}
SMOKE PASSED

$ docker compose exec -T postgres psql ... -tAc "select count(*) ..."
events=1
outbox=1
```

The concurrency test sends 100 identical signed requests with 20 worker threads and asserts
exactly one `202`, ninety-nine `200`, one event row, and one outbox row. The rate-limit test
asserts a `429` with `Retry-After` for a 1 rps / burst 1 source.

ADRs: [001-idempotency-scope](adr/001-idempotency-scope.md),
[002-secret-storage](adr/002-secret-storage.md).

CI run: <https://github.com/supremeyuma/hookrelay/actions/runs/43194695614> — green in 1m9s,
including the 100-concurrent-duplicate integration test against service Postgres and Redis.


## H3 — Outbox, worker, retries (M2)

- [x] Destinations, outbox, deliveries schema
- [x] Outbox poller with `FOR UPDATE SKIP LOCKED`
- [x] ARQ delivery worker with signed headers
- [x] Retry classification, backoff, jitter, `Retry-After`
- [x] Fake destination tests (success, 4xx, 5xx, timeout)
- [x] ADR-003

**Gate evidence**

```
$ uv run alembic upgrade head
0002 -> 0003, destinations, deliveries, and dead letters

$ uv run pytest --cov=app
103 passed; coverage 86.26%

$ uv run python scripts/smoke_delivery.py
event accepted: b956e6e4-e1df-4f4d-9c24-3011f3f2e706
event status: delivered
sink: signature valid, attempt=1 delivery_key=3a6c25d4-60e4-5603-83eb-398888ecd649
attempts recorded: 1 -> ['success']
DELIVERY SMOKE PASSED

poller log: {"msg":"poller_tick","published":1,"due":1}
worker log: delivery:<id> complete in 0.04s
```

Retry evidence is in `tests/integration/test_delivery.py`: 500,500,200 produces attempts
1→2→3 with 2s then 4s scheduled delays ±20%; `Retry-After: 7` pushes the next attempt at
least 7s out; a timeout is retryable; a 400 is permanent; exhaustion with
`max_attempts=1` creates a dead letter; two destinations produce `partial`; a failed
enqueue is recovered by `enqueue_due_deliveries`.

ADR: [003-outbox-pattern](adr/003-outbox-pattern.md).

CI run: <https://github.com/supremeyuma/hookrelay/actions/runs/43205973841> — green in 1m10s,
including the delivery integration tests against service Postgres and Redis.


## H4 — DLQ, replay, admin auth (M3)

- [x] Dead letters, API keys with scopes
- [x] Idempotent event and DLQ replay with audit
- [x] Replay storm safety test
- [x] Security notes

**Gate evidence**

```
$ uv run alembic upgrade head
0003 -> 0004, api keys and replay audit

$ uv run pytest --cov=app
115 passed; coverage 82.16%

$ uv run python scripts/smoke_replay.py
initial delivery: delivered
replay: {'replay_id': ..., 'event_id': ..., 'deliveries_created': 1}
sink deliveries: 2
duplicate replay did not send another delivery
events:read replay attempt: 403
REPLAY SMOKE PASSED
```

Replay coverage in `tests/integration/test_replay.py`: dead-letter replay with a duplicate
idempotency key returns the original result; eight concurrent same-key replays create one
attempt set; eight concurrent distinct-key replays serialize into attempts 2–9 with one
shared delivery key; `failed_only` targets only failed destinations; replaying with no
destinations returns 409. Key scope tests live in `tests/integration/test_api_keys.py`:
create/list/revoke, `events:read` cannot replay, `replay:write` cannot manage sources,
unknown/revoked keys are 401, invalid scopes are 422.

Security notes and known gaps: [security.md](security.md).

CI run: <https://github.com/supremeyuma/hookrelay/actions/runs/43210245459> — green in 1m1s,
including replay idempotency and concurrent replay tests against service Postgres/Redis.


## H5 — Observability and load (M4)

- [x] OTel traces across ingest → queue → delivery
- [x] Prometheus metrics and outbox lag
- [x] k6 ingest and delivery scripts
- [x] Reproducible load numbers with hardware
- [x] Failure modes at 10× traffic

**Gate evidence**

```
$ load/ingest.js (10 VUs, 30s, 4 API workers)
419 req/s, p95 31.78 ms, 0.00% errors, 13,085 requests

$ load/health.js (500 rps, 10s)
500 req/s, p95 1.07 ms

$ load/delivery.js (5 VUs, 20s, 1 worker)
278 req/s accepted; sink received 3,042 deliveries within ~35s

$ pytest --cov=app
115 passed; coverage 82.16%

Jaeger trace 732707222e5db301944c23ee78ed5b03: 16 spans across hookrelay (14) and
hookrelay-worker (2); operations include POST /ingest/{source_slug}, SQL BEGIN/INSERT/COMMIT,
Redis, hookrelay.deliver, and the outbound POST.
```

Metrics after the run (aggregated across 4 workers):
`hookrelay_ingest_total{result="accepted"} 23045`,
`hookrelay_ingest_total{result="duplicate"} 2455`,
`hookrelay_ingest_duration_seconds_count 25500`,
`hookrelay_outbox_lag_seconds 0` after the poller batch optimization.

The first arrival-rate attempt spawned 1,000 VUs and collapsed the host (p95 15s, ~130 rps);
the fixed-VU run above is the reproducible measurement. Analysis of the first bottlenecks at
10× is in [failure-modes.md](failure-modes.md). Trace sample:
[traces/sample-trace.json](traces/sample-trace.json).

CI run: <https://github.com/supremeyuma/hookrelay/actions/runs/43226262792> — green in 1m6s.


## H6 — Polish, deploy, release (M5)

- [ ] README, 3 ADRs, case study
- [ ] OpenAPI committed
- [ ] Demo GIF / live URL
- [ ] Production compose + Caddy
- [ ] `v1.0.0` tag, public repo, topics
- [ ] Parent `progress/log.md` updated

**Gate evidence:** _pending_
