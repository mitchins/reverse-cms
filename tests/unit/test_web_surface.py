from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from reversecrm.config import Settings
from reversecrm.db import IdempotencyConflict
from reversecrm.fixtures import fixture_files
from reversecrm.web.app import LocalSession, _encode_session, create_app

SESSION_SECRET = "test-only-session-secret-that-is-long-enough"
REVIEW_TOKEN = "test-only-review-token-that-is-long-enough"
API_TOKEN = "test-only-machine-token-that-is-long-enough"


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        data_dir=tmp_path,
        inbox_dir=tmp_path / "inbox",
        database_url=f"sqlite:///{tmp_path / 'web.sqlite3'}",
        session_secret=SESSION_SECRET,
        review_token=REVIEW_TOKEN,
        api_token=API_TOKEN,
    )


def csrf_from(response_text: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def login(client: TestClient) -> str:
    response = client.post("/login", data={"review_token": REVIEW_TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/")
    assert page.status_code == 200
    return csrf_from(page.text)


def test_submit_requires_csrf_then_enqueues_bounded_document(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        csrf = login(client)

        fixture = fixture_files()["rental-council-rates.pdf"]
        rejected = client.post(
            "/documents",
            data={"csrf": "wrong"},
            files={"document_file": ("rates.pdf", fixture, "application/pdf")},
        )
        assert rejected.status_code == 403

        accepted = client.post(
            "/documents",
            data={"csrf": csrf},
            files={"document_file": ("rates.pdf", fixture, "application/pdf")},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        status = client.get(accepted.headers["location"])
        assert status.status_code == 200
        assert "pending" in status.text
    app.state.database.engine.dispose()


def test_record_pages_reject_missing_or_tampered_session(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        assert client.get("/objects/example/documents").status_code == 401
        client.cookies.set("reversecrm_session", "tampered.value")
        assert client.get("/documents/example").status_code == 401
    app.state.database.engine.dispose()


def test_bearer_confirmation_and_relationship_query_use_core_relation(
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(tmp_path))
    core = app.state.core
    issuer = core.persistence.create_organisation(
        "Northstar City Council", "NORTHSTAR CITY COUNCIL"
    )
    rental = core.persistence.create_property(
        "Rental property", "18 EXAMPLE STREET NORTHSTAR NSW 2000", "rental"
    )
    core.persistence.create_account("Rates", "council_rates", "4821", issuer, rental)
    submission = core.submit_and_process(
        content=fixture_files()["rental-council-rates.pdf"],
        filename="rates.pdf",
        source_identity="web-test:rates",
    )
    proposal_id = submission.proposals[0].id

    with TestClient(app) as client:
        assert client.post(f"/api/proposals/{proposal_id}/confirm").status_code == 401
        confirmed = client.post(
            f"/api/proposals/{proposal_id}/confirm",
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "Idempotency-Key": "web-test-confirm-rates",
            },
        )
        assert confirmed.status_code == 200
        replayed = client.post(
            f"/api/proposals/{proposal_id}/confirm",
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "Idempotency-Key": "web-test-confirm-rates",
            },
        )
        assert replayed.status_code == 200
        assert replayed.json()["replayed"] is True
        login(client)
        related = client.get(f"/objects/{rental}/documents")
        assert related.status_code == 200
        assert "issuer_exact" in related.text
        assert "address_exact" in related.text
    app.state.database.engine.dispose()


def test_anonymous_get_does_not_mint_session_and_login_credentials_do_not_substitute(
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        landing = client.get("/", follow_redirects=False)
        assert landing.status_code == 303
        assert landing.headers["location"] == "/login"
        assert "reversecrm_session" not in client.cookies

        for wrong_credential in (SESSION_SECRET, API_TOKEN):
            rejected = client.post("/login", data={"review_token": wrong_credential})
            assert rejected.status_code == 401
            assert "reversecrm_session" not in client.cookies

        accepted = client.post(
            "/login", data={"review_token": REVIEW_TOKEN}, follow_redirects=False
        )
        assert accepted.status_code == 303
        assert "reversecrm_session" in client.cookies
        assert SESSION_SECRET not in accepted.headers.get("set-cookie", "")
        assert SESSION_SECRET not in accepted.text
    app.state.database.engine.dispose()


def test_non_ascii_credentials_are_rejected_without_server_error(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        login_response = client.post("/login", data={"review_token": "not-ascii-é"})
        assert login_response.status_code == 401

        api_response = client.post(
            "/api/proposals/not-found/confirm",
            headers=[
                (b"authorization", b"Bearer \xff"),
                (b"idempotency-key", b"non-ascii-credential"),
            ],
        )
        assert api_response.status_code == 401
    app.state.database.engine.dispose()


def test_api_accepts_only_machine_token(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        for wrong_credential in (SESSION_SECRET, REVIEW_TOKEN):
            response = client.post(
                "/api/proposals/not-found/confirm",
                headers={
                    "Authorization": f"Bearer {wrong_credential}",
                    "Idempotency-Key": "credential-separation",
                },
            )
            assert response.status_code == 401
        login(client)
        session_cookie = client.cookies.get("reversecrm_session")
        assert session_cookie is not None
        response = client.post(
            "/api/proposals/not-found/confirm",
            headers={
                "Authorization": f"Bearer {session_cookie}",
                "Idempotency-Key": "session-is-not-an-api-token",
            },
        )
        assert response.status_code == 401
    app.state.database.engine.dispose()


def test_typed_idempotency_conflict_is_mapped_to_409(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path))

    def conflict(*_args: object, **_kwargs: object) -> None:
        raise IdempotencyConflict

    app.state.core.confirm = conflict
    with TestClient(app) as client:
        response = client.post(
            "/api/proposals/existing/confirm",
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "Idempotency-Key": "reused-for-different-operation",
            },
        )
        assert response.status_code == 409
        assert response.json() == {"detail": "idempotency key conflicts with an existing operation"}
    app.state.database.engine.dispose()


def test_expired_session_is_rejected_and_logout_requires_csrf(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        expired = _encode_session(
            LocalSession("local-reviewer", "expired-csrf", 1), SESSION_SECRET.encode()
        )
        client.cookies.set("reversecrm_session", expired)
        assert client.get("/objects/example/documents").status_code == 401

        client.cookies.clear()
        csrf = login(client)
        assert client.post("/logout", data={"csrf": "wrong"}).status_code == 403
        assert client.post("/logout", data={"csrf": "☃"}).status_code == 403
        logged_out = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
        assert logged_out.status_code == 303
        assert "reversecrm_session" not in client.cookies
    app.state.database.engine.dispose()


def test_openapi_documents_explicit_error_responses(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path))
    paths = app.openapi()["paths"]

    assert set(paths["/documents"]["post"]["responses"]) >= {"401", "403", "413"}
    assert set(paths["/documents/{document_id}"]["get"]["responses"]) >= {"401", "404"}
    assert set(paths["/proposals/{proposal_id}/confirm"]["post"]["responses"]) >= {
        "401",
        "403",
    }
    assert set(paths["/api/proposals/{proposal_id}/confirm"]["post"]["responses"]) >= {
        "400",
        "401",
    }
    assert "401" in paths["/relationships/{relationship_id}"]["get"]["responses"]
    assert "401" in paths["/objects/{object_id}/documents"]["get"]["responses"]
    app.state.database.engine.dispose()
