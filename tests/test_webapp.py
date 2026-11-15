"""Smoke-тест Streamlit-интерфейса через официальный AppTest (без браузера)."""

from __future__ import annotations

from pathlib import Path

import pytest

APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"

pytest.importorskip("streamlit.testing.v1", reason="нужен streamlit")


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv("WATCHLIST", "SBER,GAZP,LKOH")
    from streamlit.testing.v1 import AppTest

    instance = AppTest.from_file(str(APP_PATH), default_timeout=120)
    instance.run()
    return instance


def test_app_renders_without_exceptions(app) -> None:
    assert not app.exception
    assert app.title[0].value.startswith("📈")
    assert len(app.chat_input) == 1
    labels = [button.label for button in app.button]
    assert "Портфель" in labels


def test_demo_banner_is_shown(app) -> None:
    infos = " ".join(info.value for info in app.info)
    assert "Демо-режим" in infos


def test_question_renders_answer_and_tool_calls(app) -> None:
    app.chat_input[0].set_value("покажи портфель").run()
    assert not app.exception
    rendered = " ".join(block.value for block in app.markdown)
    assert "Итого по портфелю" in rendered
    assert "SBER" in rendered
    expander_labels = [expander.label for expander in app.expander]
    assert any("Вызовы инструментов" in label for label in expander_labels)


def test_shortcut_button_asks_question(app) -> None:
    app.button(key="shortcut_Портфель").click().run()
    assert not app.exception
    rendered = " ".join(block.value for block in app.markdown)
    assert "Портфель" in rendered
