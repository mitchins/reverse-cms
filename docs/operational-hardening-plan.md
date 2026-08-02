# Worker and review-loop hardening plan

Status: implemented and accepted by the independent rubber duck

Scope authority: [`operational-hardening-mission.md`](operational-hardening-mission.md). The original [`frozen-mission.md`](frozen-mission.md) remains a non-regression contract.

## 1. Design decisions

### Three credentials, three purposes

- `REVERSECRM_SESSION_SECRET`: server-only HMAC signing key. Never accepted from HTTP clients.
- `REVERSECRM_REVIEW_TOKEN`: household reviewer login credential. Submitted only to `/login`; a successful constant-time comparison creates the signed session.
- `REVERSECRM_API_TOKEN`: machine bearer credential for the narrow JSON confirmation endpoint.

The first slice remains single-household and single-reviewer. There is no user database. Login failures disclose no configuration detail and should be rate-limit-ready at the proxy boundary; application-wide rate limiting is not added in this slice.

### Explicit SQLite write transactions

Implement `Database.transaction()` as an explicit write-only context: obtain a connection, issue `BEGIN IMMEDIATE` directly, then explicitly commit or roll back. Do not install a global SQLAlchemy `begin` listener: SQLAlchemy autobegin on a SELECT must not acquire a SQLite write reservation. `Database.connect()` remains an ordinary connection for read queries and explicit caller control.

Concurrency tests use independent connections and synchronise callers before they enter `Database.transaction()`. The first writer may deliberately hold the reservation while the second blocks at transaction entry; after release, the second must observe and replay the committed result. Tests must not place a barrier after `BEGIN IMMEDIATE`, which would deadlock serialized writers. A separate test proves a SELECT-only `connect()` does not begin a DBAPI transaction or block a writer.

Uniqueness constraints remain the final guard. Persistence translates expected uniqueness races into replay or a domain conflict; raw `IntegrityError` does not cross the service/API boundary.

### One processing path and one completion boundary

Delete the unused `ProcessingStore/process_one` path. The shipped worker becomes the only queued processing implementation:

```text
claim lease (short write transaction)
-> resolve immutable evidence
-> bounded OCR/extraction (no DB transaction)
-> deterministic candidate reads (no write transaction)
-> complete_processing(...) (one short BEGIN IMMEDIATE transaction)
   - verify active lease owner and expiry
   - insert/reuse extraction signals
   - revalidate and persist bounded proposals
   - mark document complete
   - mark job complete and clear lease
```

The atomic completion service owns signal-ID mapping so the worker cannot partially call three persistence methods. Existing lower-level methods may remain private for tests/migration only or be removed when no longer used.

### Worker lifecycle

`run_worker()` creates settings, database, migrations, evidence store, core service, worker ID, and logger once. `run_once(...)` receives the long-lived application and never migrates or recreates the engine. Polling remains a simple one-second loop; no scheduler framework is added.

Expected processing failures update the job and log once. Unexpected failures are logged with exception information, followed by a best-effort failure transition. A failure-transition conflict such as a lost lease is logged separately and the loop continues.

### Explicit retry

Add `reversecrm retry DOCUMENT_ID` for failed jobs. It changes only `failed -> pending`, clears error/lease fields, checks a configured maximum attempt count, records an audit event with an idempotency key, and returns success on exact replay. Replay is bound to `(document_id, actor, transition=failed_to_pending)`. Reusing the key for a different document, actor, or transition raises a typed idempotency conflict mapped to HTTP/CLI conflict behavior rather than leaking an audit uniqueness error. It cannot retry active or completed jobs.

## 2. Work packages

### Package A — transaction and atomic completion

Owner: persistence agent; exclusive ownership of database connection policy, migrations/schema if needed, and persistence write services.

Deliver:

- explicit write-only `BEGIN IMMEDIATE` transaction context, with ordinary reads proven not to reserve writes;
- concurrency-safe ingestion, proposal completion, confirmation, and retry semantics;
- domain conflict exceptions with API-safe meanings;
- atomic lease-verified completion transaction;
- retry transition and semantically bound audit record;
- multi-connection concurrency, rollback, lease-expiry, and retry tests, including concurrent duplicate retry yielding one audit event and no raw `IntegrityError`.

No schema change is expected beyond a constraint/index only if tests demonstrate it is necessary. Any change requires mediation.

### Package B — worker lifecycle and diagnostics

Owner: worker agent; no schema or authentication changes.

Deliver:

- one production processing path; delete dead protocol/path;
- initialise/migrate once and reuse application across polls;
- safe structured logging and best-effort failure transition;
- lost-lease loop-continuation test;
- worker tests covering claim, successful atomic completion, processing failure, lease loss, idle polling, and migration count;
- CLI retry wiring against Package A’s service.

### Package C — review authentication and API separation

Owner: review-boundary agent; no migrations or worker internals.

Deliver:

- validated three-secret settings and sample configuration;
- login/logout flow, signed expiring session, CSRF retention, and no anonymous session minting;
- distinct machine API bearer validation;
- domain conflict to HTTP `409` mapping, with expected replay still `200`;
- security tests proving each credential cannot substitute for another;
- corrected UI/API wording and minimal templates.

## 3. Integration and mediation

The orchestrator freezes the following interfaces before handoff:

- `Database.transaction()` explicitly issues `BEGIN IMMEDIATE`; `Database.connect()` does not implicitly reserve a write transaction for SELECTs.
- `PersistenceService.complete_processing(document_id, worker_id, extraction, proposals, validator)` owns the atomic completion.
- `PersistenceService.retry_processing_job(document_id, actor, idempotency_key)` binds replay to document, actor, and `failed_to_pending`, returning the original result or a typed conflict.
- `ConfirmationService.confirm(...)` returns the existing result on valid replay and raises a typed idempotency conflict for semantic mismatch.
- Worker logging fields are codes/IDs only.

The orchestrator reviews shared contract changes, removes integration duplication, strengthens Docker topology acceptance, and runs the exact CI commands. Agents do not broaden scope or edit another package’s owned files without mediation.

## 4. Test and acceptance sequence

1. Preserve the original 37-test suite and three anchor contracts.
2. Add deterministic concurrent persistence tests with separate connections.
3. Add worker unit/integration tests with injected extractor and logger; no sleeps.
4. Add review-boundary tests for login, logout, expiry, CSRF, credential separation, replay, and conflict.
5. Add fault-injection tests immediately before atomic completion and during failure-state update.
6. Replace the synchronous container harness with, or supplement it by, a queue-driven harness that:
   - seeds subjects;
   - submits all three anchors without synchronous processing;
   - runs worker iterations under `--network none`;
   - verifies jobs complete and proposals validate;
   - confirms through the core/API boundary;
   - retrieves documents by confirmed relationship.
7. Run Ruff, format, strict mypy, coverage, dependency audit, Compose validation, image build/tool smoke, and network-disabled queue-worker-review acceptance.

## 5. Quality gates

- All original and new tests pass; no skip can hide a missing worker path.
- Overall coverage remains at least 80%; worker and transaction/concurrency modules have meaningful branch coverage.
- Ruff, formatting, and strict mypy pass with the exact CI commands.
- No unreviewed high/critical dependency or container findings.
- Main-branch SonarQube remains fail-closed and its quality gate waits for completion.
- Logs and HTTP errors contain no document text, secrets, addresses, or account values.

## 6. Definition of done

- The real deployed queue/worker/review/query loop passes for all three anchors with container networking disabled.
- Credential separation and real reviewer login are proven by tests.
- Concurrent duplicate operations are replayed or rejected with defined domain/HTTP behavior, never raw integrity errors.
- Worker migration/initialisation occurs once, failures are diagnosable, lease loss cannot crash the polling loop, and retry is explicit/audited/bounded.
- Processing completion is atomic and the dead competing path is removed.
- Documentation accurately describes current status and boundaries.
- The independent rubber duck accepts the implementation against the two frozen missions without adding scope.
