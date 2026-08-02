# Reverse CRM

Reverse CRM is a small, self-hosted tool for connecting household documents to properties, accounts, and assets that you already track. Its first slice deliberately proves one loop: submit evidence, deterministically propose a relationship, show why, obtain human confirmation, and retrieve the document through that confirmed relationship.

It is not a sales CRM, SaaS product, mailbox synchroniser, obligation tracker, or general knowledge graph. The [frozen mission](docs/frozen-mission.md) is the scope authority.

## Current status

The project is pre-alpha. The deterministic three-anchor value loop and its operational hardening are implemented with a durable SQLite queue, bounded worker processing, explicit human confirmation, and relationship-based retrieval. The implementation has passed its local quality and container acceptance gates; this remains a pre-alpha project, not a production release.

## Local development

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), and Docker for container checks.

```sh
uv sync --extra test
uv run reversecrm generate-fixtures --output generated-fixtures
uv run ruff check .
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov --cov-report=xml
```

The three generated PDFs contain only conspicuously synthetic data. Acceptance seed and expected extraction contracts live in `tests/fixtures`.

## Run with Docker Compose

Create local configuration and replace all three example secrets with independently generated values:

```sh
cp .env.example .env
openssl rand -hex 32  # server-only session signing secret
openssl rand -hex 32  # human reviewer login token
openssl rand -hex 32  # machine API bearer token
mkdir -p inbox
docker compose build
docker compose up
```

Paste the three distinct values into `REVERSECRM_SESSION_SECRET`, `REVERSECRM_REVIEW_TOKEN`, and `REVERSECRM_API_TOKEN` in `.env` before starting. The signing secret stays server-side and is never a client credential. A reviewer signs in at `/login` with the review token; the narrow JSON confirmation endpoint accepts only the API bearer token. Review mutations remain session- and CSRF-protected.

The app publishes to loopback by default. The worker has `network_mode: none`; neither process requires Hermes or Telegram. `/data` is a named volume and `./inbox` is a bind mount for deliberately staged input. Failed worker jobs require the explicit operator retry command; they are not silently retried by the review UI.

Machine confirmation uses `POST /api/proposals/{proposal_id}/confirm` with `Authorization: Bearer <REVERSECRM_API_TOKEN>` and a non-empty `Idempotency-Key` header. An exact replay returns the original result with HTTP 200; reusing a key for different confirmation semantics returns HTTP 409.

Operator diagnostics and retries use the same image and `/data` volume:

```sh
docker compose run --rm worker integrity
docker compose run --rm worker retry DOCUMENT_ID --idempotency-key OPERATOR_RETRY_KEY
```

Only failed jobs below the bounded attempt limit can be retried. Repeating the exact retry key returns the original audited transition; using it for different semantics is rejected. The worker migrates once at startup, reuses its database/application while polling, and logs only structured job IDs, stable error codes, and exception types—never document text or extracted identifiers.

This Compose file is a development and acceptance shape, not the prescribed NAS deployment. For a NAS, preserve the contracts that app and worker share one locally locking filesystem, SQLite and evidence remain on the same host, the worker has no outbound network, the container runs non-root, and backups capture both the database and evidence volume consistently. Put remote access behind a private authenticated proxy that provides rate limiting and transport security; do not directly publish the app to the internet. The application intentionally does not provide multi-user identity management or application-wide rate limiting.

## Sonar analysis

CI emits `coverage.xml` and `test-results.xml`, then runs SonarCloud when credentials are available. Main-branch CI fails closed unless the `SONAR_TOKEN` repository secret is configured; fork pull requests, which cannot receive repository secrets, still run the remaining quality gates.

The project expects a quality gate that requires maintainability rating A on new code and no new blocker/critical issues or vulnerabilities. `sonar.qualitygate.wait=true` makes a failed SonarCloud gate fail the analysis job.

The optional Compose scanner profile can also target a compatible local SonarQube instance:

```sh
uv run pytest --cov --cov-report=xml --junitxml=test-results.xml
SONAR_HOST_URL=http://host.docker.internal:9000 SONAR_TOKEN=... docker compose --profile quality run --rm sonar-scanner
```

The scanner profile is optional and does not add SonarQube itself to the application stack.

## Security and contribution

Please read [SECURITY.md](SECURITY.md) before reporting a vulnerability. Contributions are welcome under [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). The project is licensed under Apache-2.0.
