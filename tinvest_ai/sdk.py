"""Совместимость с официальными Python SDK Т-Инвестиций.

Историческая справка (важно для деплоя):

* пакет ``tinkoff-investments`` (импорт ``tinkoff.invest``) — прежний SDK,
  с марта 2025 помечен на PyPI как quarantined и не обновляется;
* текущий официальный SDK — ``t-tech-investments`` (импорт ``t_tech.invest``),
  распространяется из реестра Т-Банка.

Модуль импортирует тот пакет, который установлен, и отдаёт единый набор имён,
поэтому остальной код не знает, какой SDK под капотом.
"""

from __future__ import annotations

from typing import Any

SDK_PACKAGE: str
SDK_IMPORT: str
SDK_AVAILABLE: bool = False

Client: Any = None
schemas: Any = None
GetAssetFundamentalsRequest: Any = None
CandleInterval: Any = None
InstrumentIdType: Any = None
InstrumentStatus: Any = None
OperationState: Any = None
OperationType: Any = None
INVEST_GRPC_API: str = "invest-public-api.tinkoff.ru"
INVEST_GRPC_API_SANDBOX: str = "sandbox-invest-public-api.tinkoff.ru"

_IMPORT_ERROR: Exception | None = None


def _load() -> None:
    global Client, schemas, GetAssetFundamentalsRequest
    global CandleInterval, InstrumentIdType, InstrumentStatus, OperationState, OperationType
    global INVEST_GRPC_API, INVEST_GRPC_API_SANDBOX
    global SDK_PACKAGE, SDK_IMPORT, SDK_AVAILABLE, _IMPORT_ERROR

    try:  # актуальный официальный SDK
        from t_tech.invest import Client as _Client  # type: ignore
        from t_tech.invest import schemas as _schemas  # type: ignore
        from t_tech.invest.constants import (  # type: ignore
            INVEST_GRPC_API as _API,
        )
        from t_tech.invest.constants import (
            INVEST_GRPC_API_SANDBOX as _SANDBOX,
        )

        SDK_PACKAGE, SDK_IMPORT = "t-tech-investments", "t_tech.invest"
    except ImportError:
        try:  # legacy-пакет (импорт tinkoff.invest)
            from tinkoff.invest import Client as _Client  # type: ignore
            from tinkoff.invest import schemas as _schemas  # type: ignore
            from tinkoff.invest.constants import (  # type: ignore
                INVEST_GRPC_API as _API,
            )
            from tinkoff.invest.constants import (
                INVEST_GRPC_API_SANDBOX as _SANDBOX,
            )

            SDK_PACKAGE, SDK_IMPORT = "tinkoff-investments", "tinkoff.invest"
        except ImportError as second_error:
            _IMPORT_ERROR = second_error
            SDK_AVAILABLE = False
            return

    Client = _Client
    schemas = _schemas
    INVEST_GRPC_API = _API
    INVEST_GRPC_API_SANDBOX = _SANDBOX
    GetAssetFundamentalsRequest = getattr(_schemas, "GetAssetFundamentalsRequest", None)
    CandleInterval = getattr(_schemas, "CandleInterval", None)
    InstrumentIdType = getattr(_schemas, "InstrumentIdType", None)
    InstrumentStatus = getattr(_schemas, "InstrumentStatus", None)
    OperationState = getattr(_schemas, "OperationState", None)
    OperationType = getattr(_schemas, "OperationType", None)
    SDK_AVAILABLE = True


_load()

INSTALL_HINT = (
    "Python SDK Т-Инвестиций не установлен. Установите актуальный пакет:\n"
    "  pip install t-tech-investments "
    "--index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple\n"
    "либо legacy-вариант: pip install tinkoff-investments"
)


def require_sdk() -> None:
    """Падает с понятным сообщением, если SDK отсутствует."""
    if not SDK_AVAILABLE:
        raise RuntimeError(INSTALL_HINT) from _IMPORT_ERROR


def enum_member(enum_name: str, member: str, default: Any = None) -> Any:
    """Достаёт элемент enum по имени, устойчиво к версиям SDK."""
    enum_cls = globals().get(enum_name)
    if enum_cls is None:
        return default
    return getattr(enum_cls, member, default)


def sdk_info() -> dict[str, Any]:
    return {
        "available": SDK_AVAILABLE,
        "package": SDK_PACKAGE if SDK_AVAILABLE else None,
        "import_path": SDK_IMPORT if SDK_AVAILABLE else None,
        "error": None if SDK_AVAILABLE else str(_IMPORT_ERROR),
    }


__all__ = [
    "Client",
    "CandleInterval",
    "GetAssetFundamentalsRequest",
    "INSTALL_HINT",
    "INVEST_GRPC_API",
    "INVEST_GRPC_API_SANDBOX",
    "InstrumentIdType",
    "InstrumentStatus",
    "OperationState",
    "OperationType",
    "SDK_AVAILABLE",
    "SDK_IMPORT",
    "SDK_PACKAGE",
    "enum_member",
    "require_sdk",
    "sdk_info",
    "schemas",
]
