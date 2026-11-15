"""Сборка рабочего окружения: клиент → инструменты → агент.

Одна точка входа для CLI и Streamlit, чтобы оба интерфейса гарантированно
работали с одинаковой конфигурацией.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .agent import DeepSeekAgent
from .config import Settings
from .demo import build_demo_client
from .offline_agent import OfflineAgent
from .tinvest_client import TInvestClient
from .tools import ToolExecutor

LOGGER = logging.getLogger(__name__)


@dataclass
class Runtime:
    """Готовое к работе окружение."""

    settings: Settings
    client: Any
    executor: ToolExecutor
    agent: Any
    demo: bool = False

    @property
    def mode_label(self) -> str:
        if self.demo:
            return "ДЕМО (синтетические данные, без внешних вызовов)"
        contour = "песочница" if self.settings.sandbox else "боевой контур"
        return f"{contour} · DeepSeek {self.settings.deepseek_model}"

    @property
    def shortcuts(self) -> dict[str, str]:
        """Готовые вопросы для UI-кнопок (приёмочные сценарии)."""
        return {
            "Портфель": "Покажи портфель",
            "Лучшая доходность": "Какая бумага дала лучшую доходность за месяц?",
            "Про бумагу": "Что там по SBER?",
            "Пополнения": "Сколько я пополнил за месяц?",
            "Вотчлист": "Какие бумаги из моего вотчлиста сейчас недооценены по P/E и ROE?",
        }

    def reset(self) -> None:
        self.agent.reset()
        if hasattr(self.executor, "clear_history"):
            self.executor.clear_history()

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def build_runtime(
    settings: Settings | None = None,
    *,
    force_demo: bool = False,
    stream: bool | None = None,
) -> Runtime:
    """Создаёт клиент, исполнитель инструментов и агента.

    Демо-режим включается, если:

    * явно попросили (``force_demo`` или ``DEMO_MODE=true``), либо
    * не хватает ключей — тогда вместо падения показываем демо с предупреждением.
    """
    settings = settings or Settings.from_env()
    if stream is not None:
        settings = _with_stream(settings, stream)

    need_demo = (
        force_demo
        or settings.demo_mode
        or not (settings.has_tinvest_credentials and settings.has_deepseek_credentials)
    )
    if need_demo:
        if not settings.demo_mode and not force_demo:
            LOGGER.warning(
                "Не заданы TINVEST_TOKEN и/или DEEPSEEK_API_KEY — запускаю демо-режим "
                "на синтетических данных. Заполните .env для работы с реальным API."
            )
        client = build_demo_client(
            watchlist=settings.watchlist or ("SBER", "GAZP", "LKOH", "YDEX", "OZON"),
            default_account_id=settings.default_account_id or "sbx-1001",
        )
        executor = ToolExecutor(client=client)
        agent = OfflineAgent(settings=settings, executor=executor)
        return Runtime(settings=settings, client=client, executor=executor, agent=agent, demo=True)

    settings.require_credentials()
    client = TInvestClient(
        token=settings.tinvest_token,
        sandbox=settings.sandbox,
        retries=settings.retries,
        cache_ttl=settings.cache_ttl,
        default_account_id=settings.default_account_id,
        watchlist=settings.watchlist,
        max_performance_positions=settings.max_performance_positions,
    )
    executor = ToolExecutor(client=client)
    agent = DeepSeekAgent(settings=settings, executor=executor)
    return Runtime(settings=settings, client=client, executor=executor, agent=agent, demo=False)


def _with_stream(settings: Settings, stream: bool) -> Settings:
    from dataclasses import replace

    return replace(settings, stream=stream)


__all__ = ["Runtime", "build_runtime"]
