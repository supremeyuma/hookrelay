# Security Notes

This is the threat model and the controls that are actually implemented. It intentionally
lists gaps; a security page that claims everything is solved is not useful.

## Assets and threat model

| Asset | Why it matters | Primary threat |
|---|---|---|
| Source signing secrets | Forging provider webhooks | Database dump, log leak, over-broad read access |
| Destination signing secrets | Forging deliveries to subscribers | Same |
| Event payloads | Customer data inside webhooks | Unauthorized API access |
| Admin/replay capability | Rewriting delivery history, causing side effects | Stolen or over-scoped API key |
| Availability | Ingest is on the provider's critical path | Body floods, retry storms, SSRF abuse |

The assumed attacker has network access to the API, may hold a low-privilege API key, and may
obtain a database snapshot. A fully compromised application host is out of scope: it holds
the decryption key in memory by necessity.

## Ingest

- **HMAC-SHA256** over `f"{timestamp}.{raw_body}"`, header
  `X-HookRelay-Signature: t=...,v1=...`; lowercase hex only.
- **Replay window** (`SIGNATURE_TOLERANCE_SECONDS`, default 300) bounds reuse of a captured
  request. Signatures are compared with `hmac.compare_digest`.
- **Body cap** (`MAX_BODY_BYTES`, default 256 KiB) is enforced while streaming and via
  `Content-Length` before HMAC work; oversized requests get `413` and no row.
- **Idempotency** is scoped to `(source_id, idempotency_key)`, so one source cannot suppress
  another's events.
- **Rate limit** is a Redis token bucket per source (`rate_limit_per_second`, `burst`).
  Invalid-signature attempts are rejected before consuming tokens; there is no per-IP limit
  on the ingest route yet (see gaps).
- **Storage**: payloads and headers are stored, but `authorization`, `cookie`, `x-api-key`,
  `x-admin-key`, and the signature header are filtered out before persistence.
- **Errors** are generic (`invalid signature`, `request body too large`); reasons such as
  "stale timestamp" are logged server-side, not returned.

## Secrets at rest

- Source and destination secrets are encrypted with **Fernet** using
  `SECRET_ENCRYPTION_KEY`; the column never holds plaintext. The key is validated at boot,
  so a malformed key fails fast.
- Secrets are returned in plaintext exactly once: on source/destination creation and on
  source secret rotation.
- Source rotation is a one-row re-encryption; the old secret stops verifying immediately.
- Logs never include secrets, signatures, or full payloads.

## API keys and authorization

- The bootstrap `ADMIN_API_KEY` is accepted with **all scopes**. It is intended for setup,
  not for automation.
- Stored keys use the format `hr_live_<random>`, are hashed with SHA-256, and are listed by a
  short prefix only. The plaintext is shown once at creation.
- Scopes are `admin:write`, `events:read`, and `replay:write`; endpoints enforce them
  server-side, not in the client.
- `DELETE /admin/api-keys/{id}` deactivates a key; the next request gets `401`.
  `last_used_at` is updated at most once per minute to avoid a write per request.
- Missing/unknown/revoked keys → `401`; valid key lacking the scope → `403`.
- The bootstrap key cannot be revoked through the API; rotate it by changing the environment
  and restarting.

## Destinations and SSRF

- Only `http`/`https` URLs are accepted; credentials in the URL are rejected.
- Unless `ALLOW_PRIVATE_DESTINATIONS=true`, the host is resolved and every resolved address
  must be globally routable. Loopback, RFC1918, link-local (including `169.254.169.254`),
  multicast, and reserved ranges are rejected. The flag exists for local development and
  tests and must stay `false` in production.
- Delivery redirects are not followed; a 3xx is treated as a permanent failure.
- Each delivery has a 10s total / 3s connect timeout and records at most 4 KiB of the
  response body.

## Replay safety

- Replay requires `replay:write` and an `Idempotency-Key`; the same key returns the original
  audit record without creating deliveries.
- Replays are **append-only**: new attempts share the original stable `delivery_key`, so
  consumers can dedupe. Historical attempt rows are never mutated or deleted.
- Destination rows are locked in sorted order during replay, so concurrent replays cannot
  reuse an attempt number.
- A single replay is capped at 1000 destinations (`413` above that).
- Every replay is audited in `replays` with the acting API key id (null for bootstrap).
- Dead-letter replay re-targets the original destination even if it is now inactive; this is
  an explicit operator action, not automatic.

## Delivery

- Every attempt is signed with the destination's secret over the canonical JSON body.
- Retries are bounded (8 attempts by default); permanent 4xx responses are not retried, which
  avoids amplifying a request flood against a broken subscriber.
- `Retry-After` is honoured but capped at one hour.
- The worker never claims "exactly once". The contract is **at-least-once with idempotent
  consumers**, backed by `X-HookRelay-Delivery-Key`.

## Known gaps / what I would do next

1. **Event payloads are not encrypted at rest.** They are jsonb and readable by anyone with
   database access. Field-level encryption or a separate encrypted blob store is the next
   step if payloads are sensitive.
2. **One global encryption key, no KMS.** Losing it makes secrets unreadable; leaking it plus
   the database defeats encryption. Move to envelope encryption with a key id per row.
3. **API keys do not expire.** Add `expires_at`, require rotation for long-lived keys, and
   optionally bind keys to an owner.
4. **No per-IP rate limit on ingest.** A source's bucket is shared; an attacker without a
   valid secret still causes a database lookup per request. Add an edge/IP limiter.
5. **Admin endpoints are not rate limited.** A stolen bootstrap key can create unlimited
   sources; add a bucket and an audit log export.
6. **`events:read` can read full payloads.** Add per-source read scopes if tenants share an
   API key.
7. **Replay limit is fixed at 1000.** Make it configurable and record the limit in the audit
   row.
