"""Цикл Function Calling в DeepSeekAgent — на фейковом OpenAI-клиенте."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any

import pytest

from tinvest_ai.agent import AgentError, DeepSeekAgent
from tinvest_ai.config import Settings
from tinvest_ai.tools import ToolExecutor


# ------------------------------------------------------------------ фейки API
def tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments)
    )


def response(content: str | None = None, tool_calls: list[Any] | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def chunk(content: str | None = None, tool_calls: list[Any] | None = None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


def tc_delta(
    index: int, call_id: str | None = None, name: str | None = None, arguments: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeCompletions:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if kwargs.get("stream"):
            return iter(item)
        return item


class FakeOpenAI:
    def __init__(self, script: list[Any]) -> None:
        self.completions = FakeCompletions(script)
        self.chat = SimpleNamespace(completions=self.completions)


class StubDataClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_portfolio(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_portfolio", kwargs))
        return {"positions_count": 2, "positions": [{"ticker": "SBER"}], "total_value": 100.0}

    def get_cash_flows(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_cash_flows", kwargs))
        return {"totals": {"RUB": {"deposits": 50_000.0}}, "operations_count": 3}


def build_agent(script: list[Any], settings: Settings) -> tuple[DeepSeekAgent, FakeOpenAI, ToolExecutor]:
    client = FakeOpenAI(script)
    executor = ToolExecutor(client=StubDataClient())
    agent = DeepSeekAgent(settings=settings, executor=executor, client=client)
    return agent, client, executor


# ---------------------------------------------------------------------- тесты
def test_tool_call_loop(settings: Settings) -> None:
    script = [
        response(tool_calls=[tool_call("call-1", "get_portfolio", '{"account_id": "acc-1"}')]),
        response(content="В портфеле 2 позиции."),
    ]
    agent, fake, executor = build_agent(script, settings)
    started: list[tuple[str, str]] = []
    finished: list[Any] = []
    answer = agent.ask(
        "покажи портфель",
        on_tool_start=lambda name, args: started.append((name, args)),
        on_tool_end=finished.append,
    )

    assert answer.text == "В портфеле 2 позиции."
    assert answer.tools_used == ["get_portfolio"]
    assert answer.rounds == 2
    assert started and started[0][0] == "get_portfolio"
    assert finished and finished[0].ok is True
    assert answer.usage["total_tokens"] == 120

    # второй запрос содержит результат инструмента
    second_request = fake.completions.requests[1]
    tool_messages = [m for m in second_request["messages"] if m["role"] == "tool"]
    assert tool_messages and tool_messages[0]["tool_call_id"] == "call-1"
    assert json.loads(tool_messages[0]["content"])["positions_count"] == 2

    # инструменты передаются в схемах, tool_choice=auto
    assert fake.completions.requests[0]["tool_choice"] == "auto"
    assert fake.completions.requests[0]["tools"]


def test_two_tools_in_one_round(settings: Settings) -> None:
    script = [
        response(
            tool_calls=[
                tool_call("c1", "get_portfolio", "{}"),
                tool_call("c2", "get_cash_flows", '{"days": 7}'),
            ]
        ),
        response(content="Готово"),
    ]
    agent, fake, executor = build_agent(script, settings)
    answer = agent.ask("портфель и пополнения за неделю")
    assert [record.name for record in answer.tool_calls] == ["get_portfolio", "get_cash_flows"]
    tool_messages = [m for m in fake.completions.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 2
    assert executor.client.calls[1] == ("get_cash_flows", {"days": 7})


def test_streaming_text(settings: Settings) -> None:
    script = [[chunk(content="При"), chunk(content="вет"), chunk(content="!")]]
    agent, fake, _ = build_agent(script, dataclasses.replace(settings, stream=True))
    tokens: list[str] = []
    answer = agent.ask("привет", on_token=tokens.append)
    assert answer.text == "Привет!"
    assert tokens == ["При", "вет", "!"]
    assert fake.completions.requests[0]["stream"] is True


def test_streaming_tool_calls(settings: Settings) -> None:
    script = [
        [
            chunk(tool_calls=[tc_delta(0, call_id="c1", name="get_portfolio", arguments='{"acc')]),
            chunk(tool_calls=[tc_delta(0, arguments='ount_id": "acc-1"}')]),
        ],
        [chunk(content="Готово.")],
    ]
    agent, fake, executor = build_agent(script, dataclasses.replace(settings, stream=True))
    answer = agent.ask("покажи портфель")
    assert answer.text == "Готово."
    assert answer.tools_used == ["get_portfolio"]
    assert executor.client.calls[0] == ("get_portfolio", {"account_id": "acc-1"})


def test_history_is_trimmed(settings: Settings) -> None:
    script = [
        response(content="раз"),
        response(content="два"),
        response(content="три"),
    ]
    agent, _, _ = build_agent(script, dataclasses.replace(settings, history_limit=2))
    agent.ask("вопрос 1")
    agent.ask("вопрос 2")
    assert len(agent.history) == 2
    assert agent.history[-1]["content"] == "два"


def test_reset_clears_history(settings: Settings) -> None:
    agent, _, _ = build_agent([response(content="ok")], settings)
    agent.ask("вопрос")
    assert agent.history
    agent.reset()
    assert agent.history == []


def test_round_limit_forces_final_answer(settings: Settings) -> None:
    script = [
        response(tool_calls=[tool_call("c1", "get_portfolio", "{}")]),
        response(tool_calls=[tool_call("c2", "get_portfolio", "{}")]),
        response(content="Итог по данным"),
    ]
    agent, fake, _ = build_agent(script, dataclasses.replace(settings, max_tool_rounds=2))
    answer = agent.ask("покажи портфель")
    assert answer.text == "Итог по данным"
    assert "tools" not in fake.completions.requests[-1]
    assert len(answer.tool_calls) == 2


def test_reasoner_model_gets_no_tools(settings: Settings) -> None:
    agent, fake, _ = build_agent(
        [response(content="ok")], dataclasses.replace(settings, deepseek_model="deepseek-reasoner")
    )
    agent.ask("привет")
    assert "tools" not in fake.completions.requests[0]


def test_tool_error_is_passed_back_to_model(settings: Settings) -> None:
    class Failing(StubDataClient):
        def get_portfolio(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("счёт не найден")

    executor = ToolExecutor(client=Failing())
    fake = FakeOpenAI(
        [
            response(tool_calls=[tool_call("c1", "get_portfolio", "{}")]),
            response(content="Не удалось получить портфель"),
        ]
    )
    agent = DeepSeekAgent(settings=settings, executor=executor, client=fake)
    answer = agent.ask("портфель")
    assert answer.tool_calls[0].ok is False
    tool_message = [m for m in fake.completions.requests[1]["messages"] if m["role"] == "tool"][0]
    assert json.loads(tool_message["content"])["error"] == "tool_failed"


def test_api_error_is_explained(settings: Settings) -> None:
    class Unauthorized(Exception):
        status_code = 401

    class FailingCompletions:
        def create(self, **kwargs: Any) -> Any:
            raise Unauthorized("invalid api key")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))
    agent = DeepSeekAgent(settings=settings, executor=ToolExecutor(client=StubDataClient()), client=fake)
    with pytest.raises(AgentError) as exc:
        agent.ask("привет")
    assert "401" in str(exc.value)
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_empty_question_raises(settings: Settings) -> None:
    agent, _, _ = build_agent([response(content="ok")], settings)
    with pytest.raises(AgentError):
        agent.ask("   ")


def test_system_prompt_contains_contour_and_watchlist(settings: Settings) -> None:
    agent, _, _ = build_agent([response(content="ok")], settings)
    prompt = agent._system_prompt
    assert "SBER" in prompt
    assert "песочниц" in prompt.lower()
    assert "GetAssetFundamentals" in prompt
