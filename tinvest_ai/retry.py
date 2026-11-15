"""Retry с экспоненциальной задержкой и полным jitter-ом.

Ретраим только "шумные" ошибки транспорта и перегрузки API:
UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED, INTERNAL, ABORTED
и сетевые исключения. Ошибки валидации (INVALID_ARGUMENT, NOT_FOUND,
PERMISSION_DENIED) пробрасываются сразу — повтор их не исправит.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

# https://grpc.github.io/grpc/core/md_doc_statuscodes.html
RETRYABLE_GRPC_CODES: frozenset[int] = frozenset({1, 4, 8, 10, 13, 14})
RETRYABLE_GRPC_NAMES: frozenset[str] = frozenset(
    {"CANCELLED", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED", "ABORTED", "INTERNAL", "UNAVAILABLE"}
)
NON_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    NotImplementedError,
)


def extract_grpc_code(exc: BaseException) -> int | str | None:
    """Достаёт gRPC-код из исключения SDK (``RequestError`` и родственники)."""
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            code = code()
        except Exception:  # pragma: no cover
            code = None
    if code is None:
        metadata = getattr(exc, "metadata", None)
        code = getattr(metadata, "code", None)
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        return code.upper().removeprefix("STATUS_CODE_").removeprefix("CODE_")
    return None


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, NON_RETRYABLE_EXCEPTIONS):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    code = extract_grpc_code(exc)
    if isinstance(code, int):
        return code in RETRYABLE_GRPC_CODES
    if isinstance(code, str):
        return code in RETRYABLE_GRPC_NAMES
    name = type(exc).__name__.lower()
    return any(marker in name for marker in ("unavailable", "timeout", "connection", "temporarily"))


def retry_call(
    func: Callable[..., T],
    *args: Any,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
    retry_if: Callable[[BaseException], bool] = is_retryable,
    logger: logging.Logger | None = None,
    operation: str = "",
    **kwargs: Any,
) -> T:
    """Вызывает ``func`` с ретраями.

    :param attempts: общее число попыток (включая первую).
    :param sleep: подменяемая функция ожидания — в тестах передаётся no-op.
    """
    attempts = max(1, attempts)
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - пробрасываем после решения о ретрае
            last_exc = exc
            if attempt >= attempts or not retry_if(exc):
                raise
            delay = random.uniform(0.0, min(max_delay, base_delay * (2 ** (attempt - 1))))
            if logger:
                logger.warning(
                    "Повтор %s/%s%s после ошибки %s: %s (пауза %.2f c)",
                    attempt,
                    attempts - 1,
                    f" для {operation}" if operation else "",
                    type(exc).__name__,
                    exc,
                    delay,
                )
            sleep(delay)
    assert last_exc is not None  # pragma: no cover - недостижимо
    raise last_exc


__all__ = [
    "is_retryable",
    "retry_call",
    "extract_grpc_code",
    "RETRYABLE_GRPC_CODES",
]
