# ADR 002: Encrypt provider secrets at rest

- **Status:** Accepted
- **Date:** 2026-11-12
- **Deciders:** HookRelay maintainer

## Context

Each source has an HMAC secret. The ingest path needs the plaintext secret on every request.
Storing it plaintext in Postgres means a database dump, a read-replica leak, or an accidental
`SELECT *` in a log exposes every provider's signing key — enough to forge webhooks.

The threat model is a compromised database snapshot or over-broad read access, not a
compromised application host (which necessarily holds the key in memory while verifying).

## Options considered

1. **Plaintext column** — simplest, fastest. A dump is a full compromise of every source.
2. **Application-level encryption with a key from the environment** — Fernet
   (AES-128-CBC + HMAC) with a single `SECRET_ENCRYPTION_KEY`. Secrets are useless in a dump
   without the key. Operational cost: key distribution and rotation.
3. **External KMS / Vault** — envelope encryption and audit logging, but adds a network
   dependency to the ingest hot path and infrastructure this project does not need.
4. **Hashing** — impossible: HMAC verification needs the original secret.

## Decision

Option 2. `SecretCipher` wraps Fernet. `sources.secret_encrypted` holds the token. The key
comes from `SECRET_ENCRYPTION_KEY`, is validated at boot, and never appears in logs, API
responses, or the repository. Secrets are returned in plaintext exactly once on create and
on `POST /admin/sources/{slug}/rotate-secret`.

Every write path encrypts; every verify path decrypts in memory and drops the plaintext
immediately. Rotation re-encrypts the single source row and invalidates the old secret.

## Consequences

- **Positive:** a database snapshot alone cannot forge webhooks; rotation is a one-row
  operation; no external service on the ingest path.
- **Negative / accepted cost:** one global key is a single point of failure. Losing it makes
  all stored secrets unreadable; leaking it plus the database defeats the protection. Key
  rotation currently requires re-encrypting all rows (not implemented).
- **What would make us revisit:** more than one operator, a compliance requirement, or a
  key-rotation SLA. Then move to envelope encryption with a KMS and store a key id per row
  so old ciphertexts remain decryptable during rotation.

## Links

- `app/core/crypto.py`
- `app/api/admin.py`
- `tests/unit/test_crypto.py`
