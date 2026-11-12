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


## H3 — Outbox, worker, retries (M2)

- [ ] Destinations, outbox, deliveries schema
- [ ] Outbox poller with `FOR UPDATE SKIP LOCKED`
- [ ] ARQ delivery worker with signed headers
- [ ] Retry classification, backoff, jitter, `Retry-After`
- [ ] Fake destination tests (success, 4xx, 5xx, timeout)
- [ ] ADR-003

**Gate evidence:** _pending_

## H4 — DLQ, replay, admin auth (M3)

- [ ] Dead letters, API keys with scopes
- [ ] Idempotent event and DLQ replay with audit
- [ ] Replay storm safety test
- [ ] Security notes

**Gate evidence:** _pending_

## H5 — Observability and load (M4)

- [ ] OTel traces across ingest → queue → delivery
- [ ] Prometheus metrics and outbox lag
- [ ] k6 ingest and delivery scripts
- [ ] Reproducible load numbers with hardware
- [ ] Failure modes at 10× traffic

**Gate evidence:** _pending_

## H6 — Polish, deploy, release (M5)

- [ ] README, 3 ADRs, case study
- [ ] OpenAPI committed
- [ ] Demo GIF / live URL
- [ ] Production compose + Caddy
- [ ] `v1.0.0` tag, public repo, topics
- [ ] Parent `progress/log.md` updated

**Gate evidence:** _pending_
