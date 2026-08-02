from pathlib import Path

import pytest
from pydantic import ValidationError

from reversecrm.config import Settings


def test_settings_default_to_loopback() -> None:
    settings = Settings(_env_file=None)

    assert settings.bind_host == "127.0.0.1"
    assert settings.database_url.startswith("sqlite:")


def test_web_secrets_fail_closed_when_any_is_missing() -> None:
    settings = Settings(
        _env_file=None,
        session_secret="s" * 32,
        review_token="r" * 32,
        api_token=None,
    )

    with pytest.raises(ValueError, match="REVERSECRM_API_TOKEN"):
        settings.require_web_secrets()


def test_web_secrets_are_validated_and_distinct() -> None:
    settings = Settings(
        _env_file=None,
        session_secret="s" * 32,
        review_token="r" * 32,
        api_token="a" * 32,
    )

    session, review, api = settings.require_web_secrets()
    assert [item.get_secret_value() for item in (session, review, api)] == [
        "s" * 32,
        "r" * 32,
        "a" * 32,
    ]


def test_web_secrets_cannot_reuse_same_value() -> None:
    settings = Settings(
        _env_file=None,
        session_secret="x" * 32,
        review_token="x" * 32,
        api_token="a" * 32,
    )

    with pytest.raises(ValueError, match="must be distinct"):
        settings.require_web_secrets()


def test_configured_directories_reject_parent_traversal() -> None:
    with pytest.raises(ValidationError, match="parent traversal"):
        Settings(_env_file=None, data_dir=Path("safe/../unsafe"))
