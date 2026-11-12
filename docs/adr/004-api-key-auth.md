# ADR 004: Scoped, hashed API keys instead of a single admin secret

- **Status:** Accepted
- **Date:** 2026-11-12
- **Deciders:** HookRelay maintainer

## Context

Ingest is authenticated by each provider's HMAC secret. Everything else — creating sources,
managing destinations, reading events, replaying — needs operator authentication. A single
`ADMIN_API_KEY` is enough to bootstrap, but it cannot be handed to a CI job that only needs
to read events, and it cannot be revoked per consumer.

The threat model includes a stolen low-privilege key. Scope separation limits the blast
radius; hashing means a database dump does not yield usable keys.

## Options considered

1. **Single bootstrap admin key only** — simplest; no revocation, no least privilege, every
   consumer can replay and create sources.
2. **Database-backed keys with scopes, stored hashed** — a key is shown once, only SHA-256 is
   stored, and the endpoint checks `admin:write`, `events:read`, or `replay:write`. Revocation
   is one row update.
3. **JWT / OAuth2 with an identity provider** — correct for a multi-user product, but adds a
   user model, token issuance, and key rotation infrastructure that this project does not
   need. It also makes the GitHub Action demo heavier.

## Decision

Option 2. The bootstrap `ADMIN_API_KEY` remains a break-glass key with all scopes and is
compared with `hmac.compare_digest`. Automation should use a database key:

- Format `hr_live_<random>`, returned once at creation.
- Stored as `sha256(raw)` plus a short prefix for display.
- Scopes are checked server-side per route; a valid key without the scope gets 403.
- Revocation (`DELETE /admin/api-keys/{id}`) deactivates the key; the next request is 401.
- `last_used_at` is updated at most once per minute to avoid a write per request.
- Every replay audit row records the acting key id (NULL for bootstrap).

## Consequences

- **Positive:** least privilege for CI/monitoring; a leaked read-only key cannot replay or
  create sources; key material is not recoverable from a database dump; revocation is
  immediate and does not require a restart.
- **Negative / accepted cost:** no key expiry or rotation policy yet; no owner/tenant binding;
  the bootstrap key is still a single break-glass secret and must be rotated via the
  environment. SHA-256 is appropriate for high-entropy random keys, not for human passwords.
- **What would make us revisit:** multiple human operators, per-tenant isolation, or a
  compliance requirement for key expiry. Then add `expires_at`, an owner, and OIDC for humans
  while keeping scoped keys for automation.

## Links

- `app/core/auth.py`, `app/core/api_keys.py`
- `docs/security.md`
- `tests/integration/test_api_keys.py`
