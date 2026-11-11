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

- [ ] Settings, structured logging, request IDs
- [ ] DB and Redis clients, lifespan
- [ ] `/healthz`, `/readyz`
- [ ] Compose (api, postgres, redis) + multi-stage Dockerfile
- [ ] Alembic wired, baseline revision
- [ ] CI: ruff, mypy, pytest, docker build

**Gate evidence:** _pending_

## H2 — Ingest, HMAC, idempotency (M1)

- [ ] Sources/events models + migration
- [ ] HMAC verification with timestamp tolerance
- [ ] Body cap, validation, per-source rate limit
- [ ] `ON CONFLICT DO NOTHING` dedupe
- [ ] Concurrency test: 100 duplicates → one event
- [ ] ADR-001, ADR-002

**Gate evidence:** _pending_

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
