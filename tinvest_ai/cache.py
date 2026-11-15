"""Минималистичный TTL-кэш без внешних зависимостей.

Нужен, чтобы один вопрос ассистента не превращался в десятки одинаковых
запросов к API (например, когда модель сначала ищет бумагу, потом берёт
свечи, потом цену).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Потокобезопасный кэш с временем жизни записей."""

    def __init__(self, ttl: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._data: dict[Hashable, tuple[float, T]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, key: Hashable) -> T | None:
        if not self.enabled():
            self.misses += 1
            return None
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if expires_at < self._clock():
                self._data.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: Hashable, value: T) -> None:
        if not self.enabled():
            return
        with self._lock:
            self._data[key] = (self._clock() + self.ttl, value)

    def get_or_set(self, key: Hashable, producer: Callable[[], T]) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = producer()
        self.set(key, value)
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:  # pragma: no cover - диагностика
        return len(self._data)

    def stats(self) -> dict[str, Any]:  # pragma: no cover - диагностика
        return {"size": len(self._data), "hits": self.hits, "misses": self.misses, "ttl": self.ttl}


__all__ = ["TTLCache"]
