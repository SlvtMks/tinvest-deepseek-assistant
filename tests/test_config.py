"""Конфигурация: парсинг окружения, маскирование секретов, режимы работы."""

from __future__ import annotations

import pytest

from tinvest_ai.config import ConfigError, Settings, mask_secret


def test_defaults_without_env() -> None:
    settings = Settings.from_env(env={}, load_dotenv_file=False)
    assert settings.sandbox is True
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-chat"
    assert settings.watchlist == ()
    assert settings.stream is False
    assert settings.has_tinvest_credentials is False


def test_parses_full_environment() -> None:
    settings = Settings.from_env(
        env={
            "TINVEST_TOKEN": "  t.abc  ",
            "DEEPSEEK_API_KEY": "sk-xyz",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/",
            "TINVEST_SANDBOX": "false",
            "WATCHLIST": "sber, gazp;LKOH",
            "LOG_LEVEL": "debug",
            "STREAM": "true",
            "API_RETRIES": "5",
        },
        load_dotenv_file=False,
    )
    assert settings.tinvest_token == "t.abc"
    assert settings.sandbox is False
    assert settings.watchlist == ("SBER", "GAZP", "LKOH")
    assert settings.log_level == "DEBUG"
    assert settings.stream is True
    assert settings.retries == 5
    assert settings.deepseek_base_url == "https://api.deepseek.com"


def test_watchlist_deduplicates() -> None:
    settings = Settings.from_env(env={"WATCHLIST": "SBER, SBER, sber, GAZP"}, load_dotenv_file=False)
    assert settings.watchlist == ("SBER", "GAZP")


def test_invalid_bool_raises() -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env={"TINVEST_SANDBOX": "может быть"}, load_dotenv_file=False)


def test_mask_secret_hides_value() -> None:
    assert mask_secret(None) == "<не задан>"
    assert mask_secret("") == "<не задан>"
    assert mask_secret("abc") == "***"
    masked = mask_secret("t.1234567890abcdef")
    assert "1234567890abcdef" not in masked
    assert masked.startswith("t.12")


def test_require_credentials_lists_missing() -> None:
    settings = Settings.from_env(env={}, load_dotenv_file=False)
    with pytest.raises(ConfigError) as exc:
        settings.require_credentials()
    assert "TINVEST_TOKEN" in str(exc.value)
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_is_offline_when_keys_missing() -> None:
    assert Settings.from_env(env={}, load_dotenv_file=False).is_offline is True
    assert (
        Settings.from_env(
            env={"TINVEST_TOKEN": "t.x", "DEEPSEEK_API_KEY": "sk-y"}, load_dotenv_file=False
        ).is_offline
        is False
    )
    assert (
        Settings.from_env(
            env={"DEMO_MODE": "true", "TINVEST_TOKEN": "t.x", "DEEPSEEK_API_KEY": "sk-y"},
            load_dotenv_file=False,
        ).is_offline
        is True
    )


def test_redacted_hides_secrets(settings: Settings) -> None:
    redacted = settings.redacted()
    assert redacted["TINVEST_TOKEN"] != settings.tinvest_token
    assert redacted["DEEPSEEK_API_KEY"] != settings.deepseek_api_key
    assert "test-key" not in redacted["DEEPSEEK_API_KEY"]
    assert settings.secrets() == [settings.tinvest_token, settings.deepseek_api_key]


def test_mode_label(settings: Settings, demo_settings: Settings) -> None:
    assert "ПЕСОЧНИЦА" in settings.mode_label
    assert "ДЕМО" in demo_settings.mode_label


def test_watchlist_parsing_keeps_order() -> None:
    settings = Settings.from_env(env={"WATCHLIST": "YDEX,OZON,SBER"}, load_dotenv_file=False)
    assert settings.watchlist == ("YDEX", "OZON", "SBER")
