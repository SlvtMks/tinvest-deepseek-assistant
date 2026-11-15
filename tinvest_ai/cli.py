"""CLI-чат на rich: «запуск одной командой».

python -m tinvest_ai                 # интерактивный чат
python -m tinvest_ai --ask "покажи портфель"
python -m tinvest_ai --demo          # демо-данные, без ключей
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from collections.abc import Sequence
from typing import Any

from . import __version__
from .config import ConfigError, Settings, mask_secret
from .logging_setup import setup_logging
from .runtime import Runtime, build_runtime
from .tools import ToolCallResult, summarize_result

LOGGER = logging.getLogger(__name__)

BANNER_ART = "[bold cyan]Т-Инвестиции × DeepSeek[/bold cyan]  ·  ассистент по портфелю"
HELP_TEXT = """\
[bold]Команды[/bold]
  [cyan]/help[/cyan]      эта справка
  [cyan]/accounts[/cyan]  список счетов
  [cyan]/tools[/cyan]     какие инструменты доступны модели
  [cyan]/config[/cyan]    текущая конфигурация (секреты скрыты)
  [cyan]/debug[/cyan]     показать/скрыть сырые ответы инструментов
  [cyan]/reset[/cyan]     очистить контекст диалога
  [cyan]/exit[/cyan]      выход

[bold]Примеры вопросов[/bold]
  • Покажи портфель
  • Какая бумага дала лучшую доходность за месяц?
  • Что там по SBER?
  • Сколько я пополнил за месяц?
  • Какие бумаги из моего вотчлиста недооценены по P/E и ROE?
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tinvest-ai",
        description="AI-ассистент по портфелю Т-Инвестиций (DeepSeek + T-Invest API, read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python -m tinvest_ai\n"
            '  python -m tinvest_ai --ask "какая бумага дала лучшую доходность за месяц"\n'
            "  python -m tinvest_ai --demo --log-level DEBUG\n"
        ),
    )
    parser.add_argument("-a", "--ask", metavar="QUESTION", help="задать один вопрос и выйти")
    parser.add_argument("--demo", action="store_true", help="демо-режим на синтетических данных")
    parser.add_argument(
        "--stream",
        dest="stream",
        action="store_true",
        default=None,
        help="печатать ответ по мере генерации (без markdown-рендера)",
    )
    parser.add_argument(
        "--no-stream",
        dest="stream",
        action="store_false",
        help="дождаться полного ответа и отрендерить markdown",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    parser.add_argument("--log-file", default=None, help="писать логи в файл")
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="напечатать конфигурацию (без секретов) и выйти",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


# --------------------------------------------------------------------- рендер
def _console() -> Any:
    try:
        from rich.console import Console
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Установите зависимости: pip install -r requirements.txt") from exc
    return Console()


def print_banner(console: Any, runtime: Runtime) -> None:
    from rich.panel import Panel
    from rich.table import Table

    settings = runtime.settings
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Режим", f"[magenta]{runtime.mode_label}[/magenta]")
    if runtime.demo:
        table.add_row("Данные", "[yellow]синтетические (демо)[/yellow]")
    else:
        table.add_row("Контур", "песочница" if settings.sandbox else "[red]боевой (реальные данные)[/red]")
        table.add_row("Модель", settings.deepseek_model)
        table.add_row("DeepSeek", settings.deepseek_base_url)
        table.add_row("Токен Т-Инвестиций", mask_secret(settings.tinvest_token))
    table.add_row("Вотчлист", ", ".join(settings.watchlist) or "[dim]не задан[/dim]")
    console.print(Panel(table, title=BANNER_ART, border_style="cyan", expand=False))
    console.print(
        "[dim]Совет: вопросы можно задавать обычным языком. /help — команды, /exit — выход.[/dim]\n"
    )


def _print_tool_start(console: Any, name: str, arguments: str) -> None:
    console.print(f"  [yellow]🔧[/yellow] [bold]{name}[/bold][dim]({_shorten(arguments, 120)})[/dim]")


def _print_tool_end(console: Any, record: ToolCallResult) -> None:
    icon = "[green]✓[/green]" if record.ok else "[red]✗[/red]"
    console.print(f"    {icon} [dim]{summarize_result(record)}[/dim]")


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def ask_once(
    console: Any,
    runtime: Runtime,
    question: str,
    *,
    stream: bool,
    debug: bool = False,
) -> str:
    """Задаёт вопрос агенту, рисует прогресс и возвращает текст ответа."""
    from rich.markdown import Markdown

    tokens: list[str] = []

    def on_token(piece: str) -> None:
        tokens.append(piece)
        console.print(piece, end="", markup=False, highlight=False, soft_wrap=True)

    show_stream = stream
    status = None
    if not show_stream:
        status = console.status("[cyan]Думаю…[/cyan]", spinner="dots")
        status.start()
    try:
        answer = runtime.agent.ask(
            question,
            stream=show_stream,
            on_token=on_token if show_stream else None,
            on_tool_start=lambda name, args: (
                status.update(f"[cyan]Инструмент {name}…[/cyan]")
                if status
                else _print_tool_start(console, name, args)
            ),
            on_tool_end=lambda record: (
                status.update("[cyan]Думаю…[/cyan]") if status else _print_tool_end(console, record)
            ),
        )
    except Exception as exc:  # noqa: BLE001 - CLI не должен падать со стектрейсом
        if status:
            status.stop()
        console.print(f"\n[red]Ошибка:[/red] {exc}")
        LOGGER.debug("Ошибка при обработке вопроса", exc_info=True)
        return ""
    finally:
        if status:
            status.stop()

    if show_stream:
        console.print()
    else:
        console.print(Markdown(answer.text))

    if debug:
        from rich.json import JSON

        for record in answer.tool_calls:
            console.print(f"[dim]— {record.name} —[/dim]")
            try:
                console.print(
                    JSON.from_data(
                        record.result if isinstance(record.result, dict) else {"result": record.result}
                    )
                )
            except Exception:  # pragma: no cover
                console.print(str(record.result)[:1000])

    usage = answer.usage or {}
    footer = (
        f"[dim]{answer.duration_s:.1f} c · инструментов: {len(answer.tool_calls)} · модель: {answer.model}"
    )
    if usage.get("total_tokens"):
        footer += f" · токенов: {usage['total_tokens']}"
    footer += "[/dim]"
    console.print(footer)
    return answer.text


# ------------------------------------------------------------------- команды
def _handle_command(console: Any, runtime: Runtime, command: str, state: dict[str, Any]) -> bool:
    from rich.json import JSON
    from rich.table import Table

    name = command.strip().lower()
    if name in ("/exit", "/quit", "/q"):
        return False
    if name in ("/help", "/h", "/?"):
        console.print(HELP_TEXT)
    elif name == "/accounts":
        try:
            data = runtime.client.list_accounts()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Не удалось получить счета:[/red] {exc}")
            return True
        table = Table(title="Счета", show_lines=False)
        table.add_column("account_id", style="cyan")
        table.add_column("Название")
        table.add_column("Тип")
        table.add_column("Статус")
        for account in data.get("accounts", []):
            table.add_row(
                account.get("account_id", "—"),
                account.get("name", "—"),
                account.get("type", "—"),
                account.get("status", "—"),
            )
        console.print(table)
    elif name == "/tools":
        table = Table(title="Инструменты (Function Calling)")
        table.add_column("Название", style="cyan", no_wrap=True)
        table.add_column("Описание")
        from .tools import TOOLS

        for tool in TOOLS:
            function = tool["function"]
            table.add_row(function["name"], _shorten(function["description"], 110))
        console.print(table)
    elif name == "/config":
        console.print(JSON.from_data(runtime.settings.redacted()))
    elif name == "/debug":
        state["debug"] = not state.get("debug", False)
        console.print(f"debug: [bold]{'вкл' if state['debug'] else 'выкл'}[/bold]")
    elif name == "/reset":
        runtime.reset()
        console.print("[green]Контекст диалога очищен.[/green]")
    else:
        console.print(f"[yellow]Неизвестная команда {name}.[/yellow] Наберите /help.")
    return True


def chat_loop(console: Any, runtime: Runtime, *, stream: bool) -> int:
    from rich.prompt import Prompt

    state: dict[str, Any] = {"debug": False}
    while True:
        try:
            question = Prompt.ask("[bold green]Вы[/bold green]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]До встречи![/dim]")
            return 0
        question = question.strip()
        if not question:
            continue
        if question.startswith("/"):
            if not _handle_command(console, runtime, question, state):
                console.print("[dim]До встречи![/dim]")
                return 0
            console.print()
            continue
        console.print()
        ask_once(console, runtime, question, stream=stream, debug=state["debug"])
        console.print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    if args.demo:
        settings = dataclasses.replace(settings, demo_mode=True)

    setup_logging(
        args.log_level or settings.log_level,
        log_file=args.log_file or settings.log_file,
        secrets=settings.secrets(),
    )

    if args.show_config:
        for key, value in settings.redacted().items():
            print(f"{key} = {value}")
        return 0

    console = _console()

    # Потоковый вывод отключаем, если term не поддерживает (перенаправление в файл).
    stream = args.stream if args.stream is not None else settings.stream
    if stream and not sys.stdout.isatty():
        stream = False

    try:
        runtime = build_runtime(settings, force_demo=args.demo)
    except ConfigError as exc:
        console.print(f"[red]Конфигурация:[/red] {exc}")
        return 2
    except RuntimeError as exc:
        console.print(f"[red]Не удалось инициализировать окружение:[/red] {exc}")
        return 3

    try:
        if args.ask:
            print_banner(console, runtime)
            text = ask_once(console, runtime, args.ask, stream=stream)
            return 0 if text else 1
        print_banner(console, runtime)
        return chat_loop(console, runtime, stream=stream)
    finally:
        runtime.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
