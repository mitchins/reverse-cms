# Frozen mission: worker and review-loop hardening

This mission follows the accepted deterministic first slice. It fixes the operational shell identified by independent review without adding new product capabilities.

## Mission

Make the deployed path uphold the same guarantees as the deterministic core:

```text
authenticated document submit
-> durable queued job
-> leased worker processing
-> atomic extraction/proposal/job completion
-> authenticated human review
-> concurrency-safe confirmation
-> relationship-based query
```

The three existing anchor fixtures remain the acceptance data. No new document domain or integration is introduced.

## Required fixes

1. Separate the server-only session signing secret, human review credential, and machine API bearer token.
2. Require a real local reviewer login before issuing a review session. Anonymous GET requests must not mint an authenticated reviewer session.
3. Make SQLite write transactions begin eagerly so concurrent read-then-write idempotency paths serialize predictably; duplicate concurrent operations must replay or return a defined conflict, never an unhandled integrity error.
4. Bind confirmation replay to the original proposal, decision, and reviewer. Reusing an idempotency key for different semantics must return a defined conflict.
5. Initialise the worker database/application once, run migrations once at startup, and reuse them across polling iterations.
6. Preserve the original processing exception in safe structured logs. A lost lease or failed state update must not crash the worker or erase the original diagnosis.
7. Provide an explicit operator retry command for failed processing jobs, with audited state transition and bounded attempts.
8. Remove the competing/dead processing implementation and expose one worker processing path.
9. Keep OCR/extraction and candidate retrieval outside write transactions, then atomically persist extraction signals, validated proposals, document completion, and job completion in one short transaction that verifies the active lease.
10. Replace decorative network assertions with a real socket-denial test or the existing network-disabled container proof.
11. Correct stale README/plan claims and describe the actual login, API credential, worker, retry, and proxy boundaries.

## Acceptance invariants

1. Session signing material is never accepted as an API or reviewer credential and is not transmitted by clients.
2. The review UI rejects unauthenticated access; successful login produces an expiring signed session and CSRF-protected mutations.
3. Machine confirmation accepts only the distinct API token and remains idempotent.
4. Two concurrent confirmations using the same idempotency key produce one decision/relation/audit event; both callers receive the same successful result or one receives a documented conflict, with no 500.
5. Same-source concurrent ingestion and concurrent proposal persistence cannot corrupt state or leak raw integrity errors.
6. A processing failure leaves a failed job with a stable safe error code and a structured log containing job/document correlation plus exception type, never OCR text, addresses, account values, or secrets.
7. Losing a lease while handling an exception does not terminate the worker loop.
8. An idle worker does not recreate the engine or run Alembic every poll.
9. A crash/fault before atomic completion exposes neither partial extraction signals nor proposals and leaves the job recoverable after lease expiry or explicit retry.
10. Retrying a failed job is explicit, audited, attempt-bounded, and idempotent.
11. The real Docker path processes all three seeded anchor submissions via queue claim and worker completion, then confirms and retrieves each relationship through the review/core API.
12. The core remains independent of Hermes, Telegram, email sync, models, vector search, obligations, and automatic subject creation.

## Explicit exclusions

- Multi-user accounts, password reset, roles, OAuth/OIDC, or an identity-management system.
- Hermes or Telegram implementation.
- New ingestion channels, document types, predicates, subject types, or matching algorithms.
- A general task framework or message broker.
- A large frontend or UI redesign.
- NAS deployment itself.

## Rubber-duck authority

The rubber duck may reject the plan or implementation only for failure of this mission, its acceptance invariants, regressions in the original frozen mission, or inaccurate verification claims. It cannot add product features or infrastructure.

