# Security policy

## Supported versions

Reverse CRM is pre-alpha. Only the latest commit on `main` receives security fixes.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting for this repository. If that is unavailable, contact the repository owner privately and include the affected revision, impact, reproduction steps using synthetic data, and any suggested mitigation.

Do not attach household evidence, credentials, database files, or extracted document text. We will acknowledge a report as soon as practical and coordinate disclosure after a fix is available.

## Deployment boundary

The application is intended for a single household on a trusted private network. It is not hardened for direct internet exposure or multi-tenant use. Bind to loopback by default and use a private authenticated reverse proxy with TLS and rate limiting for remote access.

Configure three independently generated values: the server-only session signing secret, the human review login token, and the machine API bearer token. They are deliberately non-interchangeable. Never send the signing secret to a client or reuse any value for another purpose. Logout invalidates the browser cookie; rotating the signing secret invalidates every existing session.

Run the worker without outbound network, keep `/data` on a local filesystem with reliable locking, and back up the database and evidence store together. Failed processing jobs require an explicit bounded operator retry and should be investigated using safe job/error codes rather than document text.

Container, dependency, static-analysis, and test gates reduce risk but do not replace deployment review.
