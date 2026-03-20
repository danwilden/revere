"""Strategy tool executors — create, validate, and list strategies."""
from __future__ import annotations

from backend.agents.tools.client import MedallionClient
from backend.agents.tools.schemas import (
    CreateStrategyInput,
    ListStrategiesInput,
    StrategyRecord,
    ValidateStrategyInput,
    ValidateStrategyOutput,
)


async def list_strategies(
    inp: ListStrategiesInput,
    client: MedallionClient,
) -> list[StrategyRecord]:
    """Return all persisted strategies.

    Maps to: GET /api/strategies
    """
    raw = await client.get("/api/strategies", tool_name="list_strategies")
    # The API currently returns an untyped list — validate each element.
    if isinstance(raw, list):
        return [StrategyRecord.model_validate(item) for item in raw]
    # Defensive: if the API wraps in a dict someday
    items = raw.get("strategies", raw) if isinstance(raw, dict) else []
    return [StrategyRecord.model_validate(item) for item in items]


async def create_strategy(
    inp: CreateStrategyInput,
    client: MedallionClient,
) -> StrategyRecord:
    """Persist a new strategy and return the created record.

    Maps to: POST /api/strategies
    """
    body = inp.model_dump(mode="json")
    raw = await client.post("/api/strategies", body=body, tool_name="create_strategy")
    return StrategyRecord.model_validate(raw)


async def validate_strategy(
    inp: ValidateStrategyInput,
    client: MedallionClient,
) -> ValidateStrategyOutput:
    """Run pre-flight validation on a strategy definition.

    Maps to: POST /api/strategies/{strategy_id}/validate

    Always call this before ``submit_backtest`` when using an LLM-generated
    definition to avoid wasting a backtest job on a malformed strategy.
    """
    body = {
        "definition_json": inp.definition_json,
        "strategy_type": inp.strategy_type.value,
    }
    raw = await client.post(
        f"/api/strategies/{inp.strategy_id}/validate",
        body=body,
        tool_name="validate_strategy",
    )
    return ValidateStrategyOutput.model_validate(raw)
