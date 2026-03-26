from __future__ import annotations

import os

import tyrant_core.config as config_module
from tyrant_core.config import Settings, get_settings


def test_v2_settings_get_litellm_model_handles_prefix_rules() -> None:
    plain = Settings(model="gpt-4.1", api_base="https://example.com/v1")
    already_openai = Settings(model="openai/gpt-4.1", api_base="https://example.com/v1")
    gemini = Settings(model="gemini-2.0-flash", api_base="https://example.com/v1")
    no_base = Settings(model="gpt-4.1", api_base="")

    assert plain.get_litellm_model() == "openai/gpt-4.1"
    assert already_openai.get_litellm_model() == "openai/gpt-4.1"
    assert gemini.get_litellm_model() == "gemini-2.0-flash"
    assert no_base.get_litellm_model() == "gpt-4.1"


def test_v2_settings_apply_env_only_sets_configured_values(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)

    Settings(api_key="secret-key", api_base="https://example.com/v1").apply_env()
    assert os.environ["OPENAI_API_KEY"] == "secret-key"
    assert os.environ["OPENAI_API_BASE"] == "https://example.com/v1"

    monkeypatch.setenv("OPENAI_API_KEY", "keep-key")
    monkeypatch.setenv("OPENAI_API_BASE", "keep-base")
    Settings(api_key="", api_base="").apply_env()
    assert os.environ["OPENAI_API_KEY"] == "keep-key"
    assert os.environ["OPENAI_API_BASE"] == "keep-base"


def test_v2_get_settings_is_singleton_and_reads_env(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("TYRANT_MODEL", "gpt-4.1-mini")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.model == "gpt-4.1-mini"

    monkeypatch.setattr(config_module, "_settings", None)
