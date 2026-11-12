# Production Deployment

This recipe runs the whole system on one VPS with Docker Compose and Caddy (automatic TLS).
It is intentionally single-host: enough for a demo and honest about being a single Postgres
primary. The capacity analysis in [`../docs/failure-modes.md`](../docs/failure-modes.md)
explains what changes at 10×.

## 1. Server

- Ubuntu 24.04, 2 vCPU / 4 GB RAM minimum (4 vCPU / 8 GB recommended for load tests).
- Docker Engine + Compose plugin installed.
- Ports 80 and 443 open. Do **not** expose Postgres (5432) or Redis (6379).
- A DNS `A` record for `DOMAIN` pointing at the server.

## 2. Configure

```bash
git clone git@github.com:supremeyuma/hookrelay.git
cd hookrelay
cp .env.example .env
```

Edit `.env` and set at least:

```
POSTGRES_PASSWORD=<long random>
SECRET_ENCRYPTION_KEY=<Fernet key>
ADMIN_API_KEY=<long random bootstrap key>
DOMAIN=hookrelay.example.com
WEB_CONCURRENCY=4
ALLOW_PRIVATE_DESTINATIONS=false
```

Generate the Fernet key on the server:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`DATABASE_URL` and `REDIS_URL` are overridden by the production compose file; the host-local
values in `.env` are only for running Alembic from a workstation.

## 3. Start

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
docker compose -f deploy/docker-compose.prod.yml exec api alembic upgrade head
```

Caddy requests a certificate on first boot. Check:

```bash
docker compose -f deploy/docker-compose.prod.yml ps
curl -s https://$DOMAIN/healthz
curl -s https://$DOMAIN/readyz
```

Create the first source and destination with `X-API-Key: $ADMIN_API_KEY`.

## 4. Operations

- **Migrations:** run `exec api alembic upgrade head` after every deploy that adds one.
- **Backups:** `docker compose exec -T postgres pg_dump -U hookrelay hookrelay | gzip > backup.sql.gz`
  on a schedule; test a restore before you need it.
- **Metrics:** scrape `api:8000/metrics`, `worker:9100/metrics`, `poller:9101/metrics` from
  inside the Docker network. Do not expose them publicly.
- **Tracing:** set `OTEL_EXPORTER_OTLP_ENDPOINT` to your collector; tracing is optional and
  never blocks ingest.
- **Logs:** `docker compose logs -f api worker poller`; JSON lines with request ids.
- **Upgrade:** `git pull && docker compose ... up -d --build`; the outbox and deliveries are
  durable across restarts.

## 5. Known single-host limits

- One Postgres primary; no failover.
- Redis is required for rate limiting and the queue. If it is down, ingest fails closed and
  readiness reports 503.
- Caddy terminates TLS in front of one API service. Multiple API replicas are already safe
  (stateless + shared Redis/Postgres) but need a real load balancer or Caddy upstream list.

If you deploy this, replace the placeholder `DOMAIN` and keep `.env` off git (it is ignored).
