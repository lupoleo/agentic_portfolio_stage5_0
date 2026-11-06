"""Read-only access to the IBKR connector (architecture §4.3).

The client never talks to the network by itself. It delegates every call to a
``IbkrToolTransport`` (an MCP client in production, a recorded-response replay
in tests) and refuses, before the transport is reached, any tool that is not
on the read-only allowlist. This is the technical enforcement of the
"the system never trades" invariant.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, Protocol

READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        # account and portfolio
        "get_account_summary",
        "get_account_balances",
        "get_account_positions",
        "get_account_orders",
        "get_account_trades",
        "get_pa_allocation",
        "get_pa_performance_all_periods",
        "get_flow_type",
        # contracts and market data
        "search_contracts",
        "get_price_snapshot",
        "get_price_history",
        "get_option_parameters",
        "get_option_data",
        "search_futures",
        "get_combo_identifier",
        # research content
        "search_earnings_insights",
        "get_earnings_insight",
        "get_company_themes",
        "get_company_connections",
        "search_investment_topics",
        "get_theme_details",
        # screeners (preview and read only)
        "get_screener_asset_classes",
        "get_screener_capabilities",
        "get_screener_industries",
        "get_screener_themes",
        "get_screener_templates",
        "preview_screener",
        "list_saved_screeners",
        "get_saved_screener",
        "run_saved_screener",
        # server metadata
        "whats_new",
    }
)

# Known write tools, listed so tests can prove none of them is allowed.
KNOWN_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "create_order_instruction",
        "delete_order_instruction",
        "submit_order",
        "preview_order",
        "modify_order",
        "cancel_order",
        "answer_order_question",
        "get_order_rules",
        "get_order_status",
        "create_alert",
        "update_alert",
        "delete_alert",
        "set_alert_status",
        "create_watchlist",
        "edit_watchlist",
        "delete_watchlist",
        "create_saved_screener",
        "update_saved_screener",
        "delete_saved_screener",
        "provide_customer_feedback",
    }
)


class BrokerClientError(RuntimeError):
    """Base error of the broker client."""


class ForbiddenBrokerToolError(BrokerClientError):
    """Raised when a tool outside the read-only allowlist is requested."""


class BrokerResponseError(BrokerClientError):
    """Raised when a response does not have the expected shape."""


class IbkrToolTransport(Protocol):
    """Calls one IBKR connector tool and returns its JSON object result."""

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


class ReadOnlyIbkrClient:
    """Allowlisted, call-counting facade over an ``IbkrToolTransport``."""

    def __init__(self, transport: IbkrToolTransport) -> None:
        self._transport = transport
        self.calls: Counter[str] = Counter()

    def call(self, tool: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if tool not in READ_ONLY_TOOLS:
            raise ForbiddenBrokerToolError(
                f"IBKR tool {tool!r} is not on the read-only allowlist"
            )
        self.calls[tool] += 1
        result = self._transport.call(tool, dict(arguments or {}))
        if not isinstance(result, Mapping):
            raise BrokerResponseError(f"{tool} returned {type(result).__name__}, expected an object")
        return dict(result)

    # Typed helpers for the account sync -------------------------------------------------

    def account_summary(self) -> dict[str, Any]:
        return self.call("get_account_summary")

    def account_balances(self) -> list[dict[str, Any]]:
        return _list_field(self.call("get_account_balances"), "balances", "get_account_balances")

    def account_positions(self) -> list[dict[str, Any]]:
        return _list_field(self.call("get_account_positions"), "positions", "get_account_positions")

    def account_orders(self) -> list[dict[str, Any]]:
        result = self.call("get_account_orders")
        for key in ("orders", "data"):
            if isinstance(result.get(key), list):
                return list(result[key])
        return []

    def flow_type(self) -> str:
        value = self.call("get_flow_type").get("flow_type")
        if not isinstance(value, str) or not value:
            raise BrokerResponseError("get_flow_type returned no flow_type")
        return value


def _list_field(result: Mapping[str, Any], key: str, tool: str) -> list[dict[str, Any]]:
    value = result.get(key)
    if not isinstance(value, list):
        raise BrokerResponseError(f"{tool} returned no {key!r} list")
    return [dict(item) for item in value]


class RecordedTransport:
    """Replays recorded connector responses; used by tests and offline runs.

    ``responses`` maps a tool name to either one response or a list of
    responses returned in order (the last one repeats).
    """

    def __init__(self, responses: Mapping[str, Any]) -> None:
        self._responses = {k: (list(v) if isinstance(v, list) else [v]) for k, v in responses.items()}
        self._cursor: Counter[str] = Counter()

    def call(self, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if tool not in self._responses:
            raise BrokerClientError(f"no recorded response for {tool}")
        items = self._responses[tool]
        index = min(self._cursor[tool], len(items) - 1)
        self._cursor[tool] += 1
        return items[index]
