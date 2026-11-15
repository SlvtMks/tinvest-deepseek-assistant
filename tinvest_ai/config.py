"""Конфигурация приложения.

Все настройки читаются из переменных окружения (или .env).
Секреты никогда не логируются: для них есть :func:`mask_secret`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

_TRUE = {"1", "true", "t", "yes", "y", "on", "да"}
_FALSE = {"0", "false", "f", "no", "n", "off", "нет"}


class ConfigError(RuntimeError):
    """Некорректная или неполная конфигурация."""


def mask_secret(value: str | None, keep: int = 4) -> str:
    """Возвращает безопасное для логов представление секрета."""
    if not value:
        return "<не задан>"
    value = value.strip()
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]} (len={len(value)})"


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key)
    if value is None:
        return default
    return value.strip()


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key)
    if not raw:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{key}: ожидалось логическое значение, получено {raw!r}")


def _get_int(env: Mapping[str, str], key: str, default: int, minimum: int | None = None) -> int:
    raw = _get(env, key)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:  # pragma: no cover - защита от опечаток в .env
        raise ConfigError(f"{key}: ожидалось целое число, получено {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key}: значение должно быть >= {minimum}")
    return value


def _get_list(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw = _get(env, key)
    if not raw:
        return ()
    items = [chunk.strip().upper() for chunk in raw.replace(";", ",").split(",")]
    seen: dict[str, None] = {}
    for item in items:
        if item:
            seen.setdefault(item, None)
    return tuple(seen)


@dataclass(frozen=True)
class Settings:
    """Неизменяемый снимок настроек."""

    tinvest_token: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = DEFAULT_BASE_URL
    deepseek_model: str = DEFAULT_MODEL
    sandbox: bool = True
    watchlist: tuple[str, ...] = ()
    default_account_id: str = ""
    log_level: str = "INFO"
    log_file: Path | None = None
    demo_mode: bool = False
    max_tool_rounds: int = 8
    temperature: float = 0.2
    stream: bool = False
    history_limit: int = 12
    request_timeout: float = 90.0
    retries: int = 3
    cache_ttl: int = 60
    max_performance_positions: int = 20

    # ---------------------------------------------------------------- factory
    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        load_dotenv_file: bool = True,
        dotenv_path: str | Path | None = None,
    ) -> Settings:
        """Собирает настройки из окружения.

        :param env: подмена окружения (удобно в тестах). Если ``None`` —
            читается ``os.environ`` (+ опционально файл ``.env``).
        """
        if env is None:
            if load_dotenv_file:
                try:
                    from dotenv import load_dotenv

                    load_dotenv(dotenv_path=dotenv_path, override=False)
                except ImportError:  # pragma: no cover
                    pass
            env = os.environ

        log_file_raw = _get(env, "LOG_FILE")
        watchlist = _get_list(env, "WATCHLIST")

        return cls(
            tinvest_token=_get(env, "TINVEST_TOKEN"),
            deepseek_api_key=_get(env, "DEEPSEEK_API_KEY"),
            deepseek_base_url=_get(env, "DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
            or DEFAULT_BASE_URL,
            deepseek_model=_get(env, "DEEPSEEK_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            sandbox=_get_bool(env, "TINVEST_SANDBOX", True),
            watchlist=watchlist,
            default_account_id=_get(env, "TINVEST_ACCOUNT_ID"),
            log_level=_get(env, "LOG_LEVEL", "INFO").upper() or "INFO",
            log_file=Path(log_file_raw) if log_file_raw else None,
            demo_mode=_get_bool(env, "DEMO_MODE", False),
            max_tool_rounds=_get_int(env, "MAX_TOOL_ROUNDS", 8, minimum=1),
            temperature=float(_get(env, "TEMPERATURE", "0.2") or 0.2),
            stream=_get_bool(env, "STREAM", False),
            history_limit=_get_int(env, "HISTORY_LIMIT", 12, minimum=0),
            request_timeout=float(_get(env, "REQUEST_TIMEOUT", "90") or 90),
            retries=_get_int(env, "API_RETRIES", 3, minimum=1),
            cache_ttl=_get_int(env, "CACHE_TTL", 60, minimum=0),
            max_performance_positions=_get_int(env, "MAX_PERFORMANCE_POSITIONS", 20, minimum=1),
        )

    # ------------------------------------------------------------- валидация
    @property
    def has_tinvest_credentials(self) -> bool:
        return bool(self.tinvest_token)

    @property
    def has_deepseek_credentials(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def is_offline(self) -> bool:
        """Демо-режим: синтетические данные и ответы без внешних вызовов."""
        return self.demo_mode or not (self.has_tinvest_credentials and self.has_deepseek_credentials)

    @property
    def mode_label(self) -> str:
        if self.demo_mode:
            return "ДЕМО (синтетические данные)"
        if not self.has_tinvest_credentials or not self.has_deepseek_credentials:
            return "НЕ НАСТРОЕНО"
        return "ПЕСОЧНИЦА" if self.sandbox else "БОЕВОЙ КОНТУР"

    def require_credentials(self) -> None:
        """Бросает понятную ошибку, если ключей нет."""
        missing = []
        if not self.tinvest_token:
            missing.append("TINVEST_TOKEN")
        if not self.deepseek_api_key:
            missing.append("DEEPSEEK_API_KEY")
        if missing:
            raise ConfigError(
                "Не заданы обязательные переменные окружения: "
                + ", ".join(missing)
                + ". Скопируйте .env.example в .env и заполните значения "
                "(или запустите с DEMO_MODE=true для демонстрации)."
            )

    def redacted(self) -> dict[str, str]:
        """Безопасное представление настроек для логов и UI."""
        return {
            "TINVEST_TOKEN": mask_secret(self.tinvest_token),
            "DEEPSEEK_API_KEY": mask_secret(self.deepseek_api_key),
            "DEEPSEEK_BASE_URL": self.deepseek_base_url,
            "DEEPSEEK_MODEL": self.deepseek_model,
            "TINVEST_SANDBOX": str(self.sandbox),
            "WATCHLIST": ", ".join(self.watchlist) or "<пусто>",
            "DEMO_MODE": str(self.demo_mode),
            "LOG_LEVEL": self.log_level,
        }

    def secrets(self) -> list[str]:
        """Список секретов, которые нужно вырезать из логов."""
        return [s for s in (self.tinvest_token, self.deepseek_api_key) if s]


def load_settings(**kwargs) -> Settings:
    """Удобная обёртка над :meth:`Settings.from_env`."""
    return Settings.from_env(**kwargs)


__all__ = [
    "ConfigError",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "Settings",
    "load_settings",
    "mask_secret",
]
