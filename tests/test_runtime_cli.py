"""Сборка окружения и CLI (без сети)."""

from __future__ import annotations

import pytest

from tinvest_ai.agent import DeepSeekAgent
from tinvest_ai.cli import build_parser, main
from tinvest_ai.config import Settings
from tinvest_ai.demo import DemoTInvestClient
from tinvest_ai.offline_agent import OfflineAgent
from tinvest_ai.runtime import build_runtime
from tinvest_ai.tinvest_client import TInvestClient


def test_runtime_falls_back_to_demo_without_keys() -> None:
    settings = Settings.from_env(env={}, load_dotenv_file=False)
    runtime = build_runtime(settings)
    assert runtime.demo is True
    assert isinstance(runtime.client, DemoTInvestClient)
    assert isinstance(runtime.agent, OfflineAgent)
    runtime.close()


def test_runtime_live_with_keys(settings: Settings) -> None:
    runtime = build_runtime(settings)
    assert runtime.demo is False
    assert isinstance(runtime.client, TInvestClient)
    assert isinstance(runtime.agent, DeepSeekAgent)
    assert "песочница" in runtime.mode_label
    runtime.close()


def test_runtime_force_demo_overrides_keys(settings: Settings) -> None:
    runtime = build_runtime(settings, force_demo=True)
    assert runtime.demo is True
    assert "ДЕМО" in runtime.mode_label


def test_runtime_shortcuts_cover_acceptance_scenarios() -> None:
    runtime = build_runtime(Settings.from_env(env={}, load_dotenv_file=False))
    questions = " ".join(runtime.shortcuts.values()).lower()
    assert "портфель" in questions
    assert "доходность" in questions
    assert "пополнил" in questions
    assert "вотчлиста" in questions


def test_runtime_reset() -> None:
    runtime = build_runtime(Settings.from_env(env={}, load_dotenv_file=False))
    runtime.agent.ask("покажи портфель")
    assert runtime.agent.history
    runtime.reset()
    assert runtime.agent.history == []
    assert runtime.executor.history == []


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.ask is None
    assert args.demo is False
    assert args.stream is None


def test_parser_ask_and_demo() -> None:
    args = build_parser().parse_args(["--ask", "покажи портфель", "--demo", "--no-stream"])
    assert args.ask == "покажи портфель"
    assert args.demo is True
    assert args.stream is False


def test_cli_show_config(capsys, monkeypatch) -> None:
    monkeypatch.setenv("TINVEST_TOKEN", "t.secret-token-value")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    assert main(["--show-config"]) == 0
    output = capsys.readouterr().out
    assert "TINVEST_TOKEN" in output
    assert "t.secret-token-value" not in output


def test_cli_ask_in_demo_mode(capsys, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    exit_code = main(["--ask", "покажи портфель"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Портфель" in output


def test_cli_reports_configuration_error(capsys, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    # Пустой вопрос не должен ломать приложение
    assert main(["--ask", "   "]) in (0, 1)


@pytest.mark.parametrize("flag", ["--version"])
def test_cli_version_exits(flag: str) -> None:
    with pytest.raises(SystemExit):
        main([flag])
