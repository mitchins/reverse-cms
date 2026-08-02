"""FastAPI/Jinja pages for submit, review, confirmation, and relation query."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from reversecrm.config import Settings
from reversecrm.core import CoreApplication
from reversecrm.db import Database, IdempotencyConflict
from reversecrm.evidence import EvidenceStore

SESSION_COOKIE = "reversecrm_session"
SESSION_SECONDS = 12 * 60 * 60
TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))


@dataclass(frozen=True, slots=True)
class LocalSession:
    reviewer: str
    csrf: str
    expires: int


def _encode_session(session: LocalSession, secret: bytes) -> str:
    payload = json.dumps(
        {"reviewer": session.reviewer, "csrf": session.csrf, "expires": session.expires},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret, encoded, hashlib.sha256).digest()
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _decode_session(token: str, secret: bytes) -> LocalSession:
    try:
        encoded, supplied = token.split(".", 1)
        expected = hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
        signature = base64.urlsafe_b64decode(supplied + "=" * (-len(supplied) % 4))
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        session = LocalSession(payload["reviewer"], payload["csrf"], int(payload["expires"]))
        if session.expires <= int(time.time()):
            raise ValueError
        return session
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="valid local session required") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    session_secret, review_token, api_token = settings.require_web_secrets()
    signing_key = session_secret.get_secret_value().encode()
    review_credential = review_token.get_secret_value()
    api_credential = api_token.get_secret_value()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(_database_path(settings))
    database.migrate()
    core = CoreApplication(database, EvidenceStore(settings.data_dir / "evidence"))
    app = FastAPI(title="Reverse CRM", docs_url=None, redoc_url=None)

    @app.exception_handler(IdempotencyConflict)
    def idempotency_conflict(_request: Request, _error: IdempotencyConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "idempotency key conflicts with an existing operation"},
        )

    def session_for(request: Request) -> LocalSession:
        token = request.cookies.get(SESSION_COOKIE)
        if token is None:
            raise HTTPException(status_code=401, detail="valid local session required")
        return _decode_session(token, signing_key)

    def optional_session(request: Request) -> LocalSession | None:
        token = request.cookies.get(SESSION_COOKIE)
        if token is None:
            return None
        try:
            return _decode_session(token, signing_key)
        except HTTPException:
            return None

    def require_csrf(session: LocalSession, supplied: str) -> None:
        if not secrets.compare_digest(session.csrf, supplied):
            raise HTTPException(status_code=403, detail="invalid CSRF token")

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        if optional_session(request) is not None:
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(request, "login.html", {"failed": False})

    @app.post("/login", response_class=HTMLResponse)
    def login(
        request: Request, supplied_token: Annotated[str, Form(alias="review_token")]
    ) -> Response:
        if not secrets.compare_digest(supplied_token, review_credential):
            return TEMPLATES.TemplateResponse(
                request, "login.html", {"failed": True}, status_code=401
            )
        session = LocalSession(
            settings.reviewer_id,
            secrets.token_urlsafe(24),
            int(time.time()) + SESSION_SECONDS,
        )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            _encode_session(session, signing_key),
            httponly=True,
            secure=settings.environment == "production",
            samesite="strict",
            max_age=SESSION_SECONDS,
        )
        return response

    @app.post("/logout")
    def logout(request: Request, csrf: Annotated[str, Form()]) -> RedirectResponse:
        session = session_for(request)
        require_csrf(session, csrf)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(
            SESSION_COOKIE,
            httponly=True,
            secure=settings.environment == "production",
            samesite="strict",
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    def submit_page(request: Request) -> Response:
        session = optional_session(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "submit.html",
            {"csrf": session.csrf},
        )

    @app.post("/documents")
    async def submit_document(
        request: Request,
        document_file: Annotated[UploadFile, File()],
        csrf: Annotated[str, Form()],
    ) -> RedirectResponse:
        session = session_for(request)
        require_csrf(session, csrf)
        content = await document_file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="document exceeds upload limit")
        result = core.submit(
            content=content,
            filename=document_file.filename or "document",
            source_identity=f"web:{uuid.uuid4()}",
            max_bytes=settings.max_upload_bytes,
        )
        return RedirectResponse(f"/documents/{result.document_id}", status_code=303)

    @app.get("/documents/{document_id}", response_class=HTMLResponse)
    def document_status(request: Request, document_id: str) -> HTMLResponse:
        session = session_for(request)
        job = core.persistence.get_processing_job(document_id)
        if job is None:
            raise HTTPException(status_code=404, detail="document not found")
        return TEMPLATES.TemplateResponse(
            request,
            "review.html",
            {
                "document_id": document_id,
                "state": job["state"],
                "proposals": core.proposals(document_id),
                "csrf": session.csrf,
            },
        )

    @app.post("/proposals/{proposal_id}/confirm")
    def confirm_html(
        request: Request,
        proposal_id: str,
        csrf: Annotated[str, Form()],
        idempotency_key: Annotated[str, Form()],
    ) -> RedirectResponse:
        session = session_for(request)
        require_csrf(session, csrf)
        result = core.confirm(proposal_id, session.reviewer, idempotency_key)
        return RedirectResponse(f"/relationships/{result.relationship_id}", status_code=303)

    @app.post("/api/proposals/{proposal_id}/confirm")
    def confirm_json(
        proposal_id: str,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, str | bool]:
        expected = f"Bearer {api_credential}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="bearer authentication required")
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        result = core.confirm(proposal_id, settings.reviewer_id, idempotency_key)
        return {
            "decision_id": result.decision_id,
            "relationship_id": result.relationship_id,
            "replayed": result.replayed,
        }

    @app.get("/relationships/{relationship_id}", response_class=HTMLResponse)
    def confirmed(request: Request, relationship_id: str) -> HTMLResponse:
        session_for(request)
        return TEMPLATES.TemplateResponse(
            request,
            "confirmed.html",
            {"relationship_id": relationship_id},
        )

    @app.get("/objects/{object_id}/documents", response_class=HTMLResponse)
    def related_documents(request: Request, object_id: str) -> HTMLResponse:
        session_for(request)
        return TEMPLATES.TemplateResponse(
            request,
            "related.html",
            {"object_id": object_id, "documents": core.related_documents(object_id)},
        )

    app.state.core = core
    app.state.database = database
    return app


def _database_path(settings: Settings) -> Path:
    prefix = "sqlite:///"
    if settings.database_url.startswith(prefix):
        return Path(settings.database_url.removeprefix(prefix))
    raise ValueError("the first slice requires a local SQLite database URL")
