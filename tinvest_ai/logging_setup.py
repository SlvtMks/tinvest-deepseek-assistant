"""Настройка логирования с автоматической зачисткой секретов.

Правило простое: в логи не должны попадать ни токен Т-Инвестиций, ни ключ
DeepSeek, ни заголовок ``Authorization``. Помимо точных значений секретов
вырезаются любые строки, похожие на них по формату.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Токены Т-Инвестиций начинаются с "t." и содержат длинную base64-подобную часть.
# Ключи DeepSeek — "sk-...". Плюс общий шаблон для key=value в заголовках/URL.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"t\.[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\b(authorization|api[_-]?key|token|secret|password)(\s*[:=]\s*)(\"?)([^\s\"',;]{6,})"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{10,}"),
)

REDACTED = "***REDACTED***"


class SecretRedactingFilter(logging.Filter):
    """Вырезает секреты из отформатированного сообщения лога."""

    def __init__(self, secrets: Iterable[str] = (), name: str = "") -> None:
        super().__init__(name)
        self._secrets: tuple[str, ...] = tuple(s for s in secrets if s and len(s) >= 4)

    def add_secrets(self, secrets: Iterable[str]) -> None:
        merged = {*self._secrets, *(s for s in secrets if s and len(s) >= 4)}
        self._secrets = tuple(sorted(merged, key=len, reverse=True))

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern in _PATTERNS:
            if pattern.groups >= 4:
                text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text)
            else:
                text = pattern.sub(REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - сообщение может быть кривым
            return True
        cleaned = self.redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def redact(text: str, secrets: Sequence[str] = ()) -> str:
    """Публичный хелпер: зачистить произвольную строку."""
    return SecretRedactingFilter(secrets).redact(text)


_configured = False


def setup_logging(
    level: str | int = "INFO",
    *,
    log_file: str | Path | None = None,
    secrets: Iterable[str] = (),
    force: bool = False,
) -> logging.Logger:
    """Настраивает корневой логгер.

    Вызывается один раз при старте приложения; повторные вызовы не плодят
    хендлеры (если не передан ``force=True``).
    """
    global _configured
    root = logging.getLogger()
    if _configured and not force:
        for handler in root.handlers:
            for log_filter in handler.filters:
                if isinstance(log_filter, SecretRedactingFilter):
                    log_filter.add_secrets(secrets)
        return logging.getLogger("tinvest_ai")

    numeric_level = logging.getLevelName(str(level).upper()) if not isinstance(level, int) else level
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    root.setLevel(numeric_level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    secret_filter = SecretRedactingFilter(secrets)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(secret_filter)
    root.addHandler(stream_handler)

    if log_file:
        path = Path(log_file)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(secret_filter)
        root.addHandler(file_handler)

    # Третьи стороны на DEBUG/INFO способны печатать заголовки запросов.
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "grpc"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))

    _configured = True
    return logging.getLogger("tinvest_ai")


__all__ = ["setup_logging", "redact", "SecretRedactingFilter", "REDACTED"]
