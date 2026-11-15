"""Streamlit-интерфейс ассистента.

Запуск:

    streamlit run streamlit_app.py

На Railway приложение поднимается из Dockerfile как web-сервис (см. README).
"""

from __future__ import annotations

import logging
from typing import Any

from .config import ConfigError, Settings
from .logging_setup import setup_logging
from .runtime import Runtime, build_runtime
from .tools import ToolCallResult, summarize_result

LOGGER = logging.getLogger(__name__)

_APP_TITLE = "Т-Инвестиции × DeepSeek"
_CSS = """
<style>
  .block-container { padding-top: 1.4rem; max-width: 1100px; }
  .stChatMessage { border-radius: 12px; }
  div[data-testid="stSidebarNav"] { display: none; }
  .badge {
      display:inline-block; padding:2px 10px; border-radius:999px;
      font-size:0.78rem; font-weight:600; letter-spacing:.02em;
  }
  .badge-demo { background:#fff4d6; color:#8a6100; }
  .badge-live { background:#e2f7e9; color:#146c37; }
  .badge-sbx  { background:#e6efff; color:#1b4fa8; }
</style>
"""


def _st() -> Any:
    import streamlit as st

    return st


# --------------------------------------------------------------------- runtime
def _get_runtime(st: Any, demo: bool) -> Runtime:
    """Создаёт (и кэширует в сессии) окружение под выбранный режим."""
    key = "demo" if demo else "live"
    runtime: Runtime | None = st.session_state.get("runtime")
    if runtime is not None and st.session_state.get("runtime_key") == key:
        return runtime
    if runtime is not None:
        runtime.close()
    settings = Settings.from_env()
    runtime = build_runtime(settings, force_demo=demo)
    st.session_state["runtime"] = runtime
    st.session_state["runtime_key"] = key
    st.session_state.setdefault("messages", [])
    return runtime


def _reset_chat(st: Any, runtime: Runtime) -> None:
    runtime.reset()
    st.session_state["messages"] = []


# -------------------------------------------------------------------- sidebar
def _render_sidebar(st: Any, runtime: Runtime) -> None:
    settings = runtime.settings
    has_keys = settings.has_tinvest_credentials and settings.has_deepseek_credentials
    with st.sidebar:
        st.markdown(f"### {_APP_TITLE}")
        badge = (
            '<span class="badge badge-demo">ДЕМО · синтетика</span>'
            if runtime.demo
            else (
                '<span class="badge badge-sbx">ПЕСОЧНИЦА</span>'
                if settings.sandbox
                else '<span class="badge badge-live">БОЕВОЙ КОНТУР</span>'
            )
        )
        st.markdown(badge, unsafe_allow_html=True)

        st.session_state.setdefault("demo_choice", not has_keys)
        demo = st.toggle(
            "Демо-режим",
            key="demo_choice",
            help="Синтетические данные и сценарные ответы — без ключей и без обращения к API.",
            disabled=not has_keys,
        )
        if demo != runtime.demo:
            st.session_state.pop("runtime", None)
            st.session_state["messages"] = []
            st.rerun()

        st.divider()
        st.caption("Быстрые вопросы")
        for label, question in runtime.shortcuts.items():
            if st.button(label, use_container_width=True, key=f"shortcut_{label}"):
                st.session_state["pending_question"] = question

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Очистить", use_container_width=True):
                _reset_chat(st, runtime)
                st.rerun()
        with col2:
            check = st.button("Счета", use_container_width=True)
        if check:
            try:
                accounts = runtime.client.list_accounts()
                st.session_state["accounts_preview"] = accounts
            except Exception as exc:  # noqa: BLE001
                st.session_state["accounts_preview"] = {"error": str(exc)}
        if st.session_state.get("accounts_preview"):
            st.json(st.session_state["accounts_preview"], expanded=False)

        with st.expander("Что умеет модель"):
            from .tools import TOOLS

            for tool in TOOLS:
                function = tool["function"]
                st.markdown(f"**`{function['name']}`** — {function['description'][:160]}…")

        with st.expander("Конфигурация"):
            st.json(settings.redacted(), expanded=True)
            st.caption(
                "Секреты скрыты. Значения задаются переменными окружения "
                "(.env локально, Variables на Railway)."
            )


def _render_setup_hint(st: Any, settings: Settings) -> None:
    st.warning(
        "Ключи не найдены — показаны демонстрационные данные.\n\n"
        "Чтобы работать с реальным счётом, задайте переменные окружения "
        "`TINVEST_TOKEN` (read-only токен Т-Инвестиций) и `DEEPSEEK_API_KEY`, "
        "затем перезапустите приложение."
    )
    with st.expander("Как получить токены"):
        st.markdown(
            "1. **T-Invest API** — приложение «Инвестиции» → профиль → "
            "«Настройки» → «Токены для API». Для песочницы создайте отдельный токен "
            "в разделе «Песочница».\n"
            "2. **DeepSeek** — [platform.deepseek.com](https://platform.deepseek.com) → API keys.\n"
            "3. Локально: `cp .env.example .env` и заполнить. На Railway: вкладка "
            "**Variables** у сервиса."
        )


# ----------------------------------------------------------------------- chat
def _render_tools(st: Any, records: list[ToolCallResult]) -> None:
    if not records:
        return
    with st.expander(f"🔧 Вызовы инструментов ({len(records)})", expanded=False):
        for record in records:
            icon = "✅" if record.ok else "❌"
            st.markdown(f"{icon} **`{record.name}`** — {summarize_result(record)}")
            st.code(record.to_payload()[:1500], language="json")


def _run_question(st: Any, runtime: Runtime, question: str) -> None:
    messages: list[dict[str, Any]] = st.session_state.setdefault("messages", [])
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        status = st.status("Думаю…", expanded=False)
        counter = {"tools": 0}

        def on_tool_start(name: str, arguments: str) -> None:
            counter["tools"] += 1
            status.update(label=f"Вызываю `{name}`…", state="running")
            status.write(f"🔧 `{name}` {arguments[:200]}")

        def on_tool_end(record: ToolCallResult) -> None:
            status.write(("✅ " if record.ok else "❌ ") + f"`{record.name}` — {summarize_result(record)}")

        placeholder = st.empty()
        buffer: list[str] = []

        def on_token(piece: str) -> None:
            buffer.append(piece)
            placeholder.markdown("".join(buffer))

        try:
            answer = runtime.agent.ask(
                question,
                stream=runtime.settings.stream,
                on_token=on_token if runtime.settings.stream else None,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
            )
            text = answer.text
            records = answer.tool_calls
            meta = answer
        except (ConfigError, RuntimeError) as exc:
            status.update(label="Ошибка", state="error")
            text = f"**Не удалось получить ответ:** {exc}"
            records = []
            meta = None
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Ошибка ассистента")
            status.update(label="Ошибка", state="error")
            text = f"**Не удалось получить ответ:** {type(exc).__name__}: {exc}"
            records = []
            meta = None

        status.update(
            label=f"Готово за {getattr(meta, 'duration_s', 0):.1f} c" if meta else "Ошибка",
            state="complete" if meta else "error",
            expanded=False,
        )
        placeholder.markdown(text)
        _render_tools(st, records)
        if meta is not None:
            usage = meta.usage or {}
            st.caption(
                f"{meta.model} · {getattr(meta, 'duration_s', 0):.1f} c · "
                f"инструментов: {len(records)}"
                + (f" · токенов: {usage['total_tokens']}" if usage.get("total_tokens") else "")
            )
        messages.append(
            {
                "role": "assistant",
                "content": text,
                "tools": [{"name": r.name, "ok": r.ok, "summary": summarize_result(r)} for r in records],
            }
        )


# ------------------------------------------------------------------------ main
def main() -> None:
    st = _st()
    st.set_page_config(page_title=_APP_TITLE, page_icon="📈", layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)

    settings = Settings.from_env()
    setup_logging(settings.log_level, log_file=settings.log_file, secrets=settings.secrets())

    has_keys = settings.has_tinvest_credentials and settings.has_deepseek_credentials
    if not has_keys:
        st.session_state["demo_choice"] = True
    demo = bool(st.session_state.get("demo_choice", not has_keys))
    runtime = _get_runtime(st, demo)

    st.title("📈 Ассистент по портфелю Т-Инвестиций")
    st.caption(
        "Вопросы обычным языком → DeepSeek решает, какие данные запросить у T-Invest API "
        "(счета, портфель, операции, свечи, фундаментальные показатели) и отвечает по реальным цифрам."
    )
    if runtime.demo:
        st.info(
            "**Демо-режим.** Данные синтетические, DeepSeek не вызывается — работают "
            "готовые сценарии. Задайте `TINVEST_TOKEN` и `DEEPSEEK_API_KEY`, чтобы общаться "
            "с живой моделью.",
            icon="🧪",
        )

    _render_sidebar(st, runtime)

    if not has_keys and runtime.demo:
        _render_setup_hint(st, runtime.settings)

    messages = st.session_state.setdefault("messages", [])
    if not messages:
        st.markdown("**С чего начать:**")
        cols = st.columns(len(runtime.shortcuts))
        for column, (label, question) in zip(cols, runtime.shortcuts.items(), strict=False):
            with column:
                if st.button(label, use_container_width=True, key=f"start_{label}"):
                    st.session_state["pending_question"] = question
                    st.rerun()

    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("tools"):
                with st.expander(f"🔧 Вызовы инструментов ({len(message['tools'])})", expanded=False):
                    for tool in message["tools"]:
                        icon = "✅" if tool["ok"] else "❌"
                        st.markdown(f"{icon} **`{tool['name']}`** — {tool['summary']}")

    pending = st.session_state.pop("pending_question", None)
    question = st.chat_input("Спросите про портфель, бумагу или пополнения…")
    question = question or pending
    if question:
        _run_question(st, runtime, question)


if __name__ == "__main__":  # pragma: no cover
    main()
