# Reverse CRM first-slice implementation plan

Status: implemented and accepted by the independent rubber duck

Scope authority: [`docs/frozen-mission.md`](frozen-mission.md).

Delivery record: the implemented CLI exposes fixture generation and integrity checks; fixture
subject seeding remains an acceptance-harness operation rather than a production command. The
planned Playwright smoke was replaced by focused FastAPI browser-boundary tests plus the real
network-disabled container value-loop acceptance. These are deliberate reductions, not pending
product features.

## 1. Outcome

Build one local, deterministic vertical slice:

```text
submit PDF/image
-> preserve evidence
-> extract registered signals
-> retrieve at most three existing candidates
-> validate proposal evidence codes
-> human confirms one relationship
-> query documents through that relationship
```

The executable plan contains no email sync, subject auto-creation, obligations, graph/vector storage, cloud/model extraction, Hermes, Telegram, or large frontend.

## 2. Acceptance contracts come first

Before application implementation, add synthetic seed and document fixtures for this exact matrix:

| Fixture | Existing candidates | Expected relationship | Required validated evidence | Forbidden outcome | Relationship query proof |
|---|---|---|---|---|---|
| Rental council rates | rental property, occupied home, council rates account linked to rental | document `concerns_property` rental property | `issuer_exact`, `address_exact`, `account_suffix_exact` | occupied home must not rank first | rental property’s related documents returns confirmed notice |
| Occupied-home mortgage | occupied home, rental property, mortgage account linked to occupied home | document `concerns_property` occupied home | `issuer_exact`, `address_exact`, `account_suffix_exact` | no `owns`, `is_borrower`, or liability assertion | occupied home’s related documents returns confirmed statement |
| Fridge receipt | fridge asset, appliance distractor, merchant | document `purchase_evidence_for` fridge | `merchant_alias_exact`, `model_exact`, `serial_exact` | no new asset or merchant created | fridge’s related documents returns confirmed receipt |

Each fixture test must also prove:

- no more than three proposals are persisted;
- every proposal-evidence row can be recomputed from stored extraction signals and existing records;
- confirmation writes relationship, provenance, review decision, and audit event in one short transaction;
- repeating confirmation returns the original decision and relationship without duplicates;
- replaying the same submission identity creates no new source reference, proposal, relationship, decision, or audit event;
- submitting the same bytes under a genuinely different source identity reuses the evidence blob and may add one source reference, but does not duplicate the document/proposals;
- the suite passes with network disabled and no adapter configuration.

Fixtures are generated or synthetic. No household data enters the repository.

## 3. Architecture

Use a Python 3.12 modular monolith with two commands from the same package:

- `reversecrm web`: FastAPI, Jinja2, and small HTMX forms for submit, review, and related-document query;
- `reversecrm worker`: claims only document-processing jobs and runs inspection/OCR/extraction/ranking.

SQLite is the sole database. Evidence is stored by SHA-256 on a local-locking-safe filesystem. The app and worker use the same image; the worker image includes OCRmyPDF/Tesseract. Docker Compose runs both with `/data` and `/inbox` volumes. The later NAS stack may change proxy and volume wiring, not application contracts.

Use:

- `uv` with locked dependencies;
- FastAPI, Pydantic v2, Jinja2, and minimal HTMX;
- SQLAlchemy Core and Alembic with explicit SQLite `STRICT` migrations;
- Typer for seed, worker, integrity, and fixture commands;
- pytest/pytest-cov, Ruff, mypy, and a small Playwright happy path;
- SonarQube/SonarCloud analysis in CI, plus dependency and container scanning.

Do not introduce a generic repository framework, plugin system, broker, SPA toolchain, or distributed task abstraction.

## 4. Minimal modules

```text
src/reversecrm/
  db/          connection policy, migrations, explicit queries
  evidence/    hash, atomic placement, retrieval, integrity
  domain/      predicates, evidence registry, proposal/confirmation rules
  ingest/      submission identity, validation, bounded OCR pipeline
  extract/     deterministic anchor extractors and stored signals
  match/       candidate queries, scoring, proposal evidence generation
  review/      confirmation transaction and idempotency
  web/         three small HTML surfaces and JSON endpoints
  cli.py
```

Domain code does not import FastAPI, templates, or subprocess machinery.

## 5. Minimal schema and ownership

The persistence agent exclusively owns migrations and schema changes. Other agents implement against its reviewed service/query contract and must request mediated changes rather than editing migrations.

Minimum tables:

| Table | Purpose and key constraints |
|---|---|
| `object` | Stable ID, kind (`organisation`, `property`, `account`, `asset`, `document`), label, lifecycle timestamps. |
| `organisation` | Canonical/normalised name and registered aliases used for issuer/merchant matching. |
| `property` | Normalised address and occupancy label (`rental`, `occupied`, or null). |
| `account` | Account type, suffix, issuer organisation ID, and `related_subject_id` pointing to an existing property or asset. Unique by issuer/type/suffix. |
| `asset` | Make, model, serial/order reference; partial uniqueness for stable identifiers. |
| `evidence_blob` | Unique SHA-256, byte size, detected MIME, immutable relative path. |
| `source_reference` | Unique `(source_kind, source_identity)` plus evidence blob and received metadata. A replay hits this key. |
| `document` | One logical document for the blob in this slice, document type/date, processing state, and extractor version. Unique evidence blob ID for first-slice semantics. |
| `extraction_signal` | Document ID, registered signal type, normalised value, safe display value, source locator, extractor/version. Unique per document/type/value/locator. |
| `proposal` | Document, candidate object, registered predicate, score, rank, state. Unique document/candidate/predicate; rank limited so at most three active rows per document by application transaction and tested invariant. |
| `proposal_evidence` | Proposal, closed evidence code, extraction signal ID, matched existing object ID, validation version. Unique proposal/code/signal/matched object. |
| `review_decision` | Proposal, reviewer, decision, idempotency key, timestamp. One accepted decision per proposal and unique idempotency key. |
| `relation` | Document subject, controlled predicate, existing object target, status, confirmation decision ID. Unique active document/predicate/target. |
| `evidence_link` | Confirmed relation to proposal-evidence row, providing reconstructable provenance. |
| `audit_event` | Actor, action, affected ID, correlation/idempotency key, canonical payload/hash. Unique action/idempotency key. |
| `processing_job` | Document-specific state, attempt count, lease owner/expiry, error code. No generic task types. |

SQLite startup policy: foreign keys on, WAL, synchronous FULL, busy timeout, trusted schema off. Transactions stay short. File hashing, MIME inspection, OCR, and extraction occur outside write transactions. Processing stages write durable state at boundaries; final proposal persistence and confirmation each use a short atomic transaction.

## 6. Closed extraction and evidence contract

The only stored signal types are:

```text
issuer_name
property_address
account_suffix
merchant_name
purchase_date
amount_minor
make
model
serial
order_reference
```

The only evidence codes in this slice are:

```text
issuer_exact
address_exact
account_suffix_exact
merchant_alias_exact
model_exact
serial_exact
order_reference_exact
```

Evidence codes are never accepted as free-form caller claims. The matcher creates `proposal_evidence` only through registered validators:

| Code | Required recomputable match |
|---|---|
| `issuer_exact` | `issuer_name` equals the normalised issuer organisation of an existing account whose `related_subject_id` is the property candidate. |
| `address_exact` | `property_address` equals the property candidate’s normalised address. |
| `account_suffix_exact` | `account_suffix` equals an existing account suffix and that account’s `related_subject_id` is the property candidate. |
| `merchant_alias_exact` | `merchant_name` equals a registered alias of the existing merchant; supports receipt classification but cannot alone select an asset. |
| `model_exact` | `model` equals the asset candidate’s normalised model. |
| `serial_exact` | `serial` equals the asset candidate’s normalised serial. |
| `order_reference_exact` | `order_reference` equals the asset candidate’s stored order reference. |

Before proposal commit and again before confirmation, the application loads the signal and matched records and runs the registered validator. Invalid, stale, or unknown codes reject the transaction. Every proposal needs at least one candidate-specific code (`address_exact`, `account_suffix_exact`, `model_exact`, `serial_exact`, or `order_reference_exact`); issuer/merchant evidence alone is insufficient.

Candidate retrieval uses indexed exact matches first and small deterministic scores only across matching object kinds. Ties break by stable object ID. Persist only the top three. No fuzzy, semantic, or model fallback is part of acceptance.

## 7. Minimal human and security boundary

This is a single-household slice with one configured local reviewer identity. Remote identity management is out of scope.

- Bind to loopback by default; Compose exposes only an explicitly configured private interface.
- Require a generated local bearer/session secret and secure, HTTP-only, same-site cookies for the web session.
- Mutating HTML forms require CSRF tokens; JSON confirmation requires bearer auth and an idempotency key.
- Enforce upload byte/page/time limits, detected MIME allowlist, safe filenames, non-root containers, and no worker network in the acceptance Compose profile.
- OCR receives a read-only staged input and writes only to a per-job derivative directory.
- Logs redact document text, addresses, account suffix values, and secrets.

## 8. Delivery sequence

### A. Contract and foundation

- Add the exact seed data, generated anchor documents, expected extraction JSON, and acceptance-test skeleton first.
- Scaffold package, license, README, contribution/code-of-conduct/security files, configuration, Dockerfile/Compose, and CI.
- Configure Ruff, mypy, coverage XML, Sonar project properties/workflow, dependency audit, and container scan.

Exit: clean checkout builds; frozen fixture tests are discoverable and fail only for missing implementation; Sonar analysis is runnable in CI and through a documented local profile.

### B. Persistence and evidence

- Implement strict migrations and the frozen persistence/query contract.
- Stream-hash validated uploads and atomically place evidence by SHA-256.
- Implement source replay semantics, logical document reuse, extraction-signal/proposal persistence, confirmation transaction, and relationship-based query.
- Add a minimal integrity command for database integrity plus missing/orphan evidence detection.

Exit: persistence integration tests prove constraints, idempotency, transaction rollback, and that related-document retrieval joins `relation` rather than OCR text.

### C. Deterministic value loop

- Implement direct PDF/image submission and document-specific processing jobs.
- Implement bounded OCR subprocess execution and deterministic anchor extractors.
- Implement registered candidate queries, evidence validators, scoring, stable top-three ranking, and proposal persistence.
- Add minimal pages: submit/status, review with evidence explanations, and object-related documents.
- Implement explicit confirmation with CSRF/session and JSON idempotency protection.

Exit: all three anchor contracts pass end-to-end with the acceptance environment’s network disabled.

### D. Hardening and acceptance

- Exercise malformed/oversized input, OCR timeout, path traversal, lease recovery, restart, repeat submit/confirm, IDOR/auth, and log-redaction tests.
- Run migration-from-empty, full unit/integration/end-to-end suite, coverage, static analysis, Sonar, dependency scan, and container scan.
- Run Compose acceptance on this Docker-capable machine and document NAS storage/locking, proxy, volume ownership, and backup expectations without prescribing the final stack.

Exit: definition of done below and final rubber-duck acceptance.

## 9. Delegated work and mediation

After rubber-duck plan acceptance:

1. **Foundation agent** owns package/Compose/CI/Sonar/open-source scaffold and frozen fixture generation, but no migrations.
2. **Persistence agent** solely owns migrations, DB/evidence implementation, confirmation transaction primitives, and relationship query.
3. **Value-loop agent** owns deterministic extract/match/validate services and minimal web surfaces against the persistence contract, but no migrations or CI redesign.

The orchestrator freezes shared contracts before handoff, reviews every diff, mediates contract changes, integrates in dependency order, adds missing cross-boundary tests, and runs the full acceptance suite. Agents must not add new services, schema concepts, dependencies, or features without a short mediated change request.

## 10. Quality gates

- Ruff and mypy: zero errors.
- Tests: zero failures; network-disabled anchor acceptance suite is mandatory.
- Coverage: at least 80% overall and 95% branch coverage for evidence validators, confirmation/idempotency, and auth/CSRF rules.
- Sonar: no new blocker/critical issues or vulnerabilities; maintainability rating A on new code.
- Runtime dependencies and container: no unreviewed high/critical findings.
- Manual diff review confirms no fixture-specific hard-coded IDs or text shortcuts.

## 11. Definition of done

- A user submits each anchor PDF/image, reviews validated evidence, explicitly confirms the expected relationship, and retrieves the document from the related existing object.
- Candidate generation is deterministic, stable, and persists at most three proposals.
- Evidence codes are closed, stored against extraction signals, and recomputed before proposal and confirmation.
- Same-source replay and repeat-confirm are idempotent; distinct-source identical bytes reuse evidence without duplicating the document graph.
- The mortgage flow cannot create ownership, borrower, or liability facts.
- No subject is created through ingestion.
- The core works with network disabled and contains no Hermes/Telegram/email/model dependency.
- Docker Compose, CI/Sonar, security checks, and open-source project files are reproducible.
- The rubber duck accepts the implementation solely against the frozen mission and fixtures.
