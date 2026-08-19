from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field


class LLMCallMetrics(BaseModel):
    purpose: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    estimated_cost_usd: float = 0.0


class RunMetrics(BaseModel):
    elapsed_seconds: float = 0.0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    tool_calls: int = 0
    tool_output_chars: int = 0
    estimated_cost_usd: float = 0.0
    llm_calls_detail: list[LLMCallMetrics] = Field(default_factory=list)

    def add_tool_result(self, result: str) -> None:
        self.tool_calls += 1
        self.tool_output_chars += len(result)

    def add_llm_calls(self, calls: list[LLMCallMetrics]) -> None:
        for call in calls:
            self.llm_calls += 1
            self.input_tokens += call.input_tokens
            self.output_tokens += call.output_tokens
            self.total_tokens += call.total_tokens
            self.reasoning_tokens += call.reasoning_tokens
            self.cached_input_tokens += call.cached_input_tokens
            self.estimated_cost_usd += call.estimated_cost_usd
            self.llm_calls_detail.append(call)
        self.estimated_cost_usd = round(self.estimated_cost_usd, 8)


class Timer:
    def __init__(self) -> None:
        self.started = time.monotonic()

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 3)


def response_metrics(
    response: Any,
    *,
    purpose: str,
    model: str,
    input_cost_per_million: float = 0.0,
    output_cost_per_million: float = 0.0,
) -> LLMCallMetrics:
    usage = getattr(response, "usage", None)
    input_tokens = int(_usage_value(usage, "input_tokens") or _usage_value(usage, "prompt_tokens") or 0)
    output_tokens = int(_usage_value(usage, "output_tokens") or _usage_value(usage, "completion_tokens") or 0)
    total_tokens = int(_usage_value(usage, "total_tokens") or input_tokens + output_tokens)

    output_details = _usage_value(usage, "output_tokens_details") or {}
    input_details = _usage_value(usage, "input_tokens_details") or {}
    reasoning_tokens = int(_usage_value(output_details, "reasoning_tokens") or 0)
    cached_input_tokens = int(_usage_value(input_details, "cached_tokens") or 0)

    cost = (input_tokens / 1_000_000 * input_cost_per_million) + (
        output_tokens / 1_000_000 * output_cost_per_million
    )
    return LLMCallMetrics(
        purpose=purpose,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_input_tokens=cached_input_tokens,
        estimated_cost_usd=round(cost, 8),
    )


def _usage_value(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
