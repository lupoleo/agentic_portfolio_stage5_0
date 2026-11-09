"""Final E2E step: IBKR order instruction awaiting the holder's approval
(STAGE_5_0_ARCHITECTURE revision 5, §4.4).

Python turns the accepted ExecutionPlan into one deterministic
``OrderInstructionRequest`` and persists it next to the run. Python has no
write channel to IBKR: the assistant holding the IBKR connector creates the
instruction with exactly these fields (``create_order_instruction``, flow
``REVIEW_AND_APPROVE``), and the instruction id and deep link are recorded
back with ``record_instruction``. An instruction is not an order: it goes live
only when the account holder submits it in IBKR.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.cio.models import ExecutionPlan, ExecutionSide

SCHEMA = "stage5-order-instruction-request-v1"
CONNECTOR_TOOL = "create_order_instruction"
REQUIRED_FLOW = "REVIEW_AND_APPROVE"


class OrderInstructionError(RuntimeError):
    pass


class ConnectorArguments(BaseModel):
    """Exactly what is passed to the connector's ``create_order_instruction``."""

    model_config = ConfigDict(frozen=True)

    contract_id_ex: str
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: float | None = Field(default=None, gt=0)
    time_in_force: Literal["DAY", "GTC"] = "DAY"


class OrderInstructionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_id: str = SCHEMA
    request_id: str
    created_at: datetime
    execution_plan_id: str
    opportunity_id: str
    dry_run_id: str
    account_state_version: str
    state_origin: str
    ticker: str
    instrument_description: str | None
    market: str | None
    currency: str
    connector_tool: str = CONNECTOR_TOOL
    required_flow_type: str = REQUIRED_FLOW
    arguments: ConnectorArguments
    protective_stop: float | None  # not part of the instruction: the holder adds it in IBKR
    target_1: float | None
    target_2: float | None
    estimated_cost: float
    estimated_cost_eur: float
    estimated_max_loss_eur: float | None
    rehearsal: bool
    simulated_state: bool
    requires_holder_confirmation: bool
    blocked_reason: str | None = None
    status: Literal["PENDING_CREATION", "BLOCKED", "CREATED"] = "PENDING_CREATION"
    instruction_id: str | None = None
    deep_link: str | None = None
    recorded_at: datetime | None = None


def _conid(instrument_id: str) -> str:
    prefix, _, conid = instrument_id.partition(":")
    if prefix != "IBKR" or not conid.isdigit():
        raise OrderInstructionError(f"instrument {instrument_id} is not an IBKR listing (IBKR:<conid>)")
    return conid


def build_request(plan: ExecutionPlan, *, dry_run_id: str, version_id: str, state_origin: str,
                  cash_available: dict[str, float], rehearsal: bool, now: datetime) -> OrderInstructionRequest:
    """One instruction for one accepted ExecutionPlan. ``cash_available``: real
    IBKR cash per currency (the affordability guard)."""

    side = {ExecutionSide.BUY: "BUY", ExecutionSide.SELL_SHORT: "SELL"}[plan.execution_side]
    quantity = int(plan.quantity)
    if quantity != plan.quantity or quantity <= 0:
        raise OrderInstructionError(f"quantity {plan.quantity} is not a whole number of shares")
    order_type = "LIMIT" if plan.order_type.upper() == "LIMIT" else "MARKET"
    limit = plan.entry_price or plan.reference_price if order_type == "LIMIT" else None
    if order_type == "LIMIT" and limit is None:
        raise OrderInstructionError("LIMIT plan without an entry price")
    arguments = ConnectorArguments(contract_id_ex=_conid(plan.instrument_id), side=side, quantity=quantity,
                                   order_type=order_type, limit_price=round(limit, 4) if limit else None)
    price = limit or plan.reference_price
    if price is None:
        raise OrderInstructionError("the plan has no price to estimate the cost")
    cost = quantity * price
    fx = plan.fx_to_eur or 1.0
    simulated = state_origin != "REAL_IBKR"

    blocked = None
    if rehearsal:
        blocked = "REHEARSAL opportunity: promoted below the policy threshold on copied state"
    elif side == "BUY":
        cash = cash_available.get(plan.currency.value, 0.0) + cash_available.get("EUR", 0.0) / fx \
            if plan.currency.value != "EUR" else cash_available.get("EUR", 0.0)
        if cost > cash + 1e-9:
            blocked = f"cost {cost:.2f} {plan.currency.value} exceeds the real IBKR cash available ({cash:.2f})"

    identity = {"plan": plan.execution_plan_id, "version": version_id, "arguments": arguments.model_dump()}
    request_id = "oir-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    return OrderInstructionRequest(
        request_id=request_id, created_at=now, execution_plan_id=plan.execution_plan_id,
        opportunity_id=plan.opportunity_id, dry_run_id=dry_run_id, account_state_version=version_id,
        state_origin=state_origin, ticker=plan.underlying, instrument_description=plan.instrument_description,
        market=plan.market, currency=plan.currency.value, arguments=arguments, protective_stop=plan.stop_price,
        target_1=plan.target_1, target_2=plan.target_2, estimated_cost=round(cost, 2),
        estimated_cost_eur=round(cost * fx, 2), estimated_max_loss_eur=plan.estimated_max_loss_eur,
        rehearsal=rehearsal, simulated_state=simulated, requires_holder_confirmation=simulated,
        blocked_reason=blocked, status="BLOCKED" if blocked else "PENDING_CREATION",
    )


def write_request(request: OrderInstructionRequest, directory: Path) -> Path:
    """Idempotent: an existing request with the same id is kept (it may already be CREATED)."""

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{request.request_id}.json"
    if not path.exists():
        path.write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_request(path: Path) -> OrderInstructionRequest:
    return OrderInstructionRequest.model_validate_json(path.read_text(encoding="utf-8"))


def record_instruction(path: Path, *, instruction_id: str, deep_link: str, now: datetime) -> OrderInstructionRequest:
    request = load_request(path)
    if request.status == "BLOCKED":
        raise OrderInstructionError(f"request is BLOCKED: {request.blocked_reason}")
    if request.status == "CREATED":
        if request.instruction_id == instruction_id:
            return request
        raise OrderInstructionError(f"request already has instruction {request.instruction_id}")
    if not instruction_id.strip() or not deep_link.startswith("https://"):
        raise OrderInstructionError("instruction id and an https deep link are required")
    created = request.model_copy(update={"status": "CREATED", "instruction_id": instruction_id.strip(),
                                         "deep_link": deep_link, "recorded_at": now})
    path.write_text(created.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return created


def describe(request: OrderInstructionRequest) -> list[str]:
    a = request.arguments
    price = f"LIMIT {a.limit_price}" if a.order_type == "LIMIT" else "MARKET"
    lines = [
        f"order_instruction_request: {request.request_id} status={request.status}",
        f"  {a.side} {a.quantity} {request.ticker} (conid {a.contract_id_ex}, {request.market}) {price} "
        f"{a.time_in_force} ~{request.estimated_cost} {request.currency} (~{request.estimated_cost_eur} EUR)",
        f"  protective stop {request.protective_stop} (add it in IBKR: not part of the instruction)",
    ]
    if request.simulated_state:
        lines.append("  simulated portfolio state: the holder must confirm this instruction explicitly")
    if request.blocked_reason:
        lines.append(f"  BLOCKED: {request.blocked_reason}")
    return lines
