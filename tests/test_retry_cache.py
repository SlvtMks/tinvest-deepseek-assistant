"""Retry-политика и TTL-кэш."""

from __future__ import annotations

import pytest

from tinvest_ai.cache import TTLCache
from tinvest_ai.retry import extract_grpc_code, is_retryable, retry_call


class GrpcError(Exception):
    def __init__(self, code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.code = code


def test_retry_succeeds_after_failures() -> None:
    attempts = {"count": 0}
    delays: list[float] = []

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("network")
        return "ok"

    result = retry_call(flaky, attempts=5, sleep=delays.append)
    assert result == "ok"
    assert attempts["count"] == 3
    assert len(delays) == 2
    assert all(delay >= 0 for delay in delays)


def test_retry_gives_up_after_attempts() -> None:
    def always_fail() -> None:
        raise TimeoutError("slow")

    with pytest.raises(TimeoutError):
        retry_call(always_fail, attempts=3, sleep=lambda _: None)


def test_retry_does_not_retry_validation_errors() -> None:
    calls = {"count": 0}

    def bad() -> None:
        calls["count"] += 1
        raise ValueError("неверный тикер")

    with pytest.raises(ValueError):
        retry_call(bad, attempts=5, sleep=lambda _: None)
    assert calls["count"] == 1


@pytest.mark.parametrize("code,expected", [(14, True), (4, True), (3, False), (7, False)])
def test_is_retryable_grpc_codes(code: int, expected: bool) -> None:
    assert is_retryable(GrpcError(code)) is expected


def test_extract_grpc_code_handles_callable_and_names() -> None:
    class WithCallable(Exception):
        def code(self) -> str:
            return "STATUS_CODE_UNAVAILABLE"

    assert extract_grpc_code(GrpcError(8)) == 8
    assert extract_grpc_code(WithCallable()) == "UNAVAILABLE"
    assert extract_grpc_code(ValueError("x")) is None


def test_retry_passes_through_kwargs() -> None:
    def add(a: int, b: int = 0) -> int:
        return a + b

    assert retry_call(add, 2, b=3, attempts=1) == 5


def test_ttlcache_expires() -> None:
    now = {"value": 0.0}
    cache: TTLCache[int] = TTLCache(ttl=10, clock=lambda: now["value"])
    cache.set("key", 42)
    assert cache.get("key") == 42
    now["value"] = 11
    assert cache.get("key") is None


def test_ttlcache_get_or_set_calls_producer_once() -> None:
    calls = {"count": 0}

    def producer() -> str:
        calls["count"] += 1
        return "value"

    cache: TTLCache[str] = TTLCache(ttl=100, clock=lambda: 0.0)
    assert cache.get_or_set("k", producer) == "value"
    assert cache.get_or_set("k", producer) == "value"
    assert calls["count"] == 1


def test_ttlcache_disabled() -> None:
    cache: TTLCache[int] = TTLCache(ttl=0)
    cache.set("a", 1)
    assert cache.get("a") is None
    assert cache.enabled() is False


def test_ttlcache_invalidate() -> None:
    cache: TTLCache[int] = TTLCache(ttl=100, clock=lambda: 0.0)
    cache.set("a", 1)
    cache.invalidate()
    assert cache.get("a") is None
