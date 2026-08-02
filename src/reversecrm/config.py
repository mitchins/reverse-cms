"""Validated runtime settings shared by the web and worker processes."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, sourced from ``REVERSECRM_`` environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="REVERSECRM_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    data_dir: Path = Path("data")
    inbox_dir: Path = Path("inbox")
    database_url: str = "sqlite:///data/reversecrm.sqlite3"
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8080, ge=1, le=65535)
    session_secret: SecretStr | None = None
    review_token: SecretStr | None = None
    api_token: SecretStr | None = None
    reviewer_id: str = Field(default="local-reviewer", min_length=1, max_length=100)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    max_document_pages: int = Field(default=50, ge=1, le=500)
    ocr_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("data_dir", "inbox_dir")
    @classmethod
    def reject_parent_traversal(cls, value: Path) -> Path:
        if ".." in value.parts:
            raise ValueError("configured directories must not contain parent traversal")
        return value

    def require_session_secret(self) -> SecretStr:
        """Return the web secret or fail closed with an actionable error."""
        if self.session_secret is None or len(self.session_secret.get_secret_value()) < 32:
            raise ValueError("REVERSECRM_SESSION_SECRET must contain at least 32 characters")
        return self.session_secret

    def require_web_secrets(self) -> tuple[SecretStr, SecretStr, SecretStr]:
        """Validate and return non-interchangeable web credentials."""

        configured = {
            "REVERSECRM_SESSION_SECRET": self.session_secret,
            "REVERSECRM_REVIEW_TOKEN": self.review_token,
            "REVERSECRM_API_TOKEN": self.api_token,
        }
        values: dict[str, SecretStr] = {}
        for name, value in configured.items():
            if value is None or len(value.get_secret_value()) < 32:
                raise ValueError(f"{name} must contain at least 32 characters")
            values[name] = value
        raw_values = [value.get_secret_value() for value in values.values()]
        if len(set(raw_values)) != len(raw_values):
            raise ValueError("session, review, and API secrets must be distinct")
        return (
            values["REVERSECRM_SESSION_SECRET"],
            values["REVERSECRM_REVIEW_TOKEN"],
            values["REVERSECRM_API_TOKEN"],
        )
