import pytest

from reversecrm.config import Settings


@pytest.mark.acceptance
def test_acceptance_environment_has_no_adapter_configuration() -> None:
    forbidden_settings = {
        "hermes_token",
        "telegram_token",
        "openai_api_key",
        "anthropic_api_key",
    }

    assert forbidden_settings.isdisjoint(Settings.model_fields)
