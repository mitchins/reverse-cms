# Contributing

Thank you for helping make Reverse CRM useful and maintainable.

## Scope and design

Start with `docs/frozen-mission.md` and `docs/implementation-plan.md`. Changes should keep the core local, deterministic, and independent of optional adapters. Prefer explicit domain code over frameworks or generic abstractions. New services, schema concepts, runtime dependencies, or product features need an issue explaining the user value and maintenance cost.

Never commit real household documents, addresses, identifiers, credentials, or extracted text. Tests and examples must use generated synthetic data.

## Development workflow

1. Create a focused branch and test that demonstrates the intended behaviour.
2. Run `uv sync --extra test`.
3. Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy src`.
4. Run `uv run pytest --cov --cov-report=xml`; overall coverage must remain at least 80%.
5. If containers change, run `docker build -t reverse-crm:local .` and scan it with Trivy.
6. Explain behaviour, security impact, and migration impact in the pull request.

Commits should be small enough to review. Avoid drive-by formatting or unrelated dependency updates.

## Pull requests

Pull requests must preserve source replay and confirmation idempotency, evidence validation, bounded candidate retrieval, and relationship-based query semantics. The acceptance suite is authoritative. Reviews may reject feature growth outside the frozen mission even when the code works.

By contributing, you agree that your contribution is licensed under Apache-2.0.

