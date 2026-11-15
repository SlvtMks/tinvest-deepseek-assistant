"""Агент DeepSeek с Function Calling.

Цикл простой и предсказуемый:

1. отправляем вопрос + схемы инструментов в DeepSeek;
2. если модель вернула ``tool_calls`` — выполняем их через :class:`ToolExecutor`
   и отправляем результаты обратно как сообщения ``role=tool``;
3. повторяем, пока модель не ответит текстом (или пока не кончится лимит
   раундов — тогда просим финальный ответ без инструментов).

Поддерживается стриминг финального ответа: токены отдаются в ``on_token``,
а вызовы инструментов — в ``on_tool_start``/``on_tool_end``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .prompts import build_system_prompt
from .tools import TOOLS, ToolCallResult, ToolExecutor

LOGGER = logging.getLogger(__name__)

TokenCallback = Callable[[str], None]
ToolStartCallback = Callable[[str, str], None]
ToolEndCallback = Callable[[ToolCallResult], None]


class AgentError(RuntimeError):
    """Ошибка обращения к LLM с человекочитаемым текстом."""


@dataclass
class Answer:
    """Ответ ассистента за один ход."""

    text: str
    tool_calls: list[ToolCallResult] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    rounds: int = 0
    duration_s: float = 0.0

    @property
    def tools_used(self) -> list[str]:
        return [record.name for record in self.tool_calls]


def _dump_usage(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    for attr in ("model_dump", "dict", "to_dict"):
        method = getattr(usage, attr, None)
        if callable(method):
            try:
                return method()
            except Exception:  # pragma: no cover
                continue
    return {
        key: getattr(usage, key)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, key, None) is not None
    }


class DeepSeekAgent:
    """Диалоговый агент поверх DeepSeek (OpenAI-совместимый API)."""

    def __init__(
        self,
        settings: Settings,
        executor: ToolExecutor,
        *,
        client: Any | None = None,
        system_prompt: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.executor = executor
        self.model = settings.deepseek_model
        self.logger = logger or LOGGER
        self._client = client
        self._history: list[dict[str, Any]] = []
        self._system_prompt = system_prompt or build_system_prompt(
            sandbox=settings.sandbox, watchlist=settings.watchlist
        )

    # ------------------------------------------------------------------ client
    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise AgentError("Пакет openai не установлен: pip install openai") from exc
            if not self.settings.deepseek_api_key:
                raise AgentError(
                    "Не задан DEEPSEEK_API_KEY. Добавьте ключ в .env "
                    "(или запустите в демо-режиме: DEMO_MODE=true)."
                )
            self._client = OpenAI(
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.request_timeout,
                max_retries=max(0, self.settings.retries - 1),
            )
            self.logger.info(
                "DeepSeek клиент создан: base_url=%s, model=%s",
                self.settings.deepseek_base_url,
                self.model,
            )
        return self._client

    # ---------------------------------------------------------------- история
    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def reset(self) -> None:
        """Сбрасывает контекст диалога (инструменты и кэш не трогаем)."""
        self._history.clear()

    def _build_messages(self, question: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system_prompt}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": question})
        return messages

    def _remember(self, question: str, answer: str) -> None:
        limit = max(0, self.settings.history_limit)
        self._history.append({"role": "user", "content": question})
        self._history.append({"role": "assistant", "content": answer})
        if limit and len(self._history) > limit:
            self._history = self._history[-limit:]

    # -------------------------------------------------------------- основной
    def ask(
        self,
        question: str,
        *,
        stream: bool | None = None,
        on_token: TokenCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
        on_tool_end: ToolEndCallback | None = None,
    ) -> Answer:
        """Задаёт вопрос и возвращает ответ, выполняя tool calls по пути."""
        question = (question or "").strip()
        if not question:
            raise AgentError("Пустой вопрос.")

        started = time.perf_counter()
        stream = self.settings.stream if stream is None else stream
        messages = self._build_messages(question)
        records: list[ToolCallResult] = []
        usage: dict[str, Any] = {}
        rounds = 0
        text = ""

        while rounds <= self.settings.max_tool_rounds:
            rounds += 1
            allow_tools = rounds <= self.settings.max_tool_rounds
            outcome = (
                self._stream_completion(messages, allow_tools, on_token)
                if stream
                else self._completion(messages, allow_tools)
            )
            if outcome.get("usage"):
                usage = outcome["usage"]

            tool_calls = outcome.get("tool_calls") or []
            if not tool_calls:
                text = (outcome.get("content") or "").strip()
                if text:
                    break
                # Пустой ответ без tool calls — просим модель ответить текстом.
                messages.append(
                    {
                        "role": "user",
                        "content": "Ответь текстом на исходный вопрос, используя уже полученные данные.",
                    }
                )
                continue

            messages.append(
                {
                    "role": "assistant",
                    "content": outcome.get("content") or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments_raw"] or "{}",
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )

            for call in tool_calls:
                if on_tool_start is not None:
                    on_tool_start(call["name"], call["arguments_raw"] or "{}")
                record = self.executor.execute_json(call["name"], call["arguments_raw"])
                records.append(record)
                if on_tool_end is not None:
                    on_tool_end(record)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": record.to_payload(),
                    }
                )

        if not text:
            text = (
                "Не удалось получить ответ: исчерпан лимит раундов вызова инструментов "
                f"({self.settings.max_tool_rounds}). Попробуйте уточнить вопрос."
            )

        duration = time.perf_counter() - started
        self._remember(question, text)
        self.logger.info(
            "Ответ за %.1f c, раундов: %s, инструментов: %s, токенов: %s",
            duration,
            rounds,
            ", ".join(r.name for r in records) or "нет",
            usage.get("total_tokens", "?"),
        )
        return Answer(
            text=text,
            tool_calls=records,
            usage=usage,
            model=self.model,
            rounds=rounds,
            duration_s=duration,
        )

    # ------------------------------------------------------------ completions
    def _request_kwargs(self, messages: list[dict[str, Any]], allow_tools: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.settings.temperature,
        }
        # deepseek-reasoner не поддерживает function calling — не ломаем запрос.
        if allow_tools and "reasoner" not in self.model.lower():
            kwargs["tools"] = TOOLS
            kwargs["tool_choice"] = "auto"
        return kwargs

    def _completion(self, messages: list[dict[str, Any]], allow_tools: bool) -> dict[str, Any]:
        try:
            response = self.client.chat.completions.create(**self._request_kwargs(messages, allow_tools))
        except Exception as exc:  # noqa: BLE001
            raise AgentError(self._explain_error(exc)) from exc

        choice = response.choices[0]
        message = choice.message
        tool_calls = [
            {
                "id": getattr(call, "id", "") or f"call_{index}",
                "name": getattr(getattr(call, "function", None), "name", "") or "",
                "arguments_raw": getattr(getattr(call, "function", None), "arguments", "") or "",
            }
            for index, call in enumerate(getattr(message, "tool_calls", None) or [])
        ]
        return {
            "content": getattr(message, "content", "") or "",
            "tool_calls": tool_calls,
            "usage": _dump_usage(getattr(response, "usage", None)),
        }

    def _stream_completion(
        self,
        messages: list[dict[str, Any]],
        allow_tools: bool,
        on_token: TokenCallback | None,
    ) -> dict[str, Any]:
        try:
            stream = self.client.chat.completions.create(
                **self._request_kwargs(messages, allow_tools), stream=True
            )
        except Exception as exc:  # noqa: BLE001
            raise AgentError(self._explain_error(exc)) from exc

        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        usage: dict[str, Any] = {}

        try:
            for chunk in stream:
                chunk_usage = _dump_usage(getattr(chunk, "usage", None))
                if chunk_usage:
                    usage = chunk_usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue
                piece = getattr(delta, "content", None)
                if piece:
                    content_parts.append(piece)
                    if on_token is not None:
                        on_token(piece)
                for index, call in enumerate(getattr(delta, "tool_calls", None) or []):
                    slot_index = getattr(call, "index", index) or 0
                    slot = tool_calls.setdefault(slot_index, {"id": "", "name": "", "arguments_raw": ""})
                    if getattr(call, "id", None):
                        slot["id"] = call.id
                    function = getattr(call, "function", None)
                    if function is not None:
                        if getattr(function, "name", None):
                            slot["name"] += function.name
                        if getattr(function, "arguments", None):
                            slot["arguments_raw"] += function.arguments
        except Exception as exc:  # noqa: BLE001
            raise AgentError(self._explain_error(exc)) from exc

        ordered = [tool_calls[index] for index in sorted(tool_calls)]
        for position, call in enumerate(ordered):
            if not call["id"]:
                call["id"] = f"call_{position}"
        return {
            "content": "".join(content_parts),
            "tool_calls": ordered,
            "usage": usage,
        }

    # ----------------------------------------------------------------- errors
    @staticmethod
    def _explain_error(exc: Exception) -> str:
        """Превращает ошибку OpenAI-клиента в понятный текст."""
        name = type(exc).__name__
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "response", None), "status_code", None
        )
        message = str(exc) or name
        if status == 401:
            return "DeepSeek отклонил ключ (401). Проверьте DEEPSEEK_API_KEY в .env."
        if status == 402:
            return "На балансе DeepSeek недостаточно средств (402). Пополните счёт или смените ключ."
        if status == 429:
            return "Слишком много запросов к DeepSeek (429). Подождите немного и повторите."
        if status in (500, 502, 503, 504):
            return f"DeepSeek временно недоступен ({status}). Попробуйте ещё раз через минуту."
        if "timeout" in name.lower() or "timeout" in message.lower():
            return (
                "Превышено время ожидания ответа DeepSeek. Проверьте сеть или увеличьте "
                "REQUEST_TIMEOUT в .env."
            )
        if "connection" in name.lower() or "connect" in message.lower():
            return (
                f"Нет соединения с DeepSeek ({message}). Проверьте сеть и DEEPSEEK_BASE_URL "
                "(по умолчанию https://api.deepseek.com)."
            )
        return f"Ошибка обращения к DeepSeek: {message}"


def tool_calls_to_messages(records: Iterable[ToolCallResult]) -> list[str]:
    """Утилита для отладки: JSON-представление выполненных вызовов."""
    return [
        json.dumps({"tool": r.name, "arguments": r.arguments, "ok": r.ok}, ensure_ascii=False)
        for r in records
    ]


__all__ = ["AgentError", "Answer", "DeepSeekAgent", "tool_calls_to_messages"]
