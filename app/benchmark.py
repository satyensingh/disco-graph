from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .agent import CodingAgent
from .models import BenchmarkResponse, BenchmarkStrategyResult
from .tools import run_command


def run_benchmark(
    repo: Path,
    *,
    repo_id: str,
    task: str,
    strategies: list[str],
    max_steps: int | None,
    validation_command: str | None,
    auto_index: bool,
) -> BenchmarkResponse:
    results: list[BenchmarkStrategyResult] = []
    for strategy in strategies:
        agent = CodingAgent()
        response = agent.run(
            repo,
            task,
            max_steps=max_steps,
            auto_index=auto_index if strategy == "graph_adaptive" else False,
            isolated_worktree=True,
            retrieval_strategy=strategy,
        )
        validation = _run_validation(response.worktree, validation_command)
        signals = _correctness_signals(response, validation)
        results.append(
            BenchmarkStrategyResult(
                strategy=strategy,  # type: ignore[arg-type]
                response=response,
                validation=validation,
                correctness_signals=signals,
            )
        )

    return BenchmarkResponse(
        repo_id=repo_id,
        task=task,
        results=results,
        comparison=_comparison(results),
    )


def _run_validation(worktree: dict[str, Any] | None, command: str | None) -> dict[str, Any] | None:
    if not command:
        return None
    active_path = (worktree or {}).get("active_path")
    if not active_path:
        return {"command": command, "exit_code": None, "output": "No active worktree path available."}
    output = run_command(Path(str(active_path)), command)
    exit_code = None
    match = re.search(r"exit_code=(\d+)", output)
    if match:
        exit_code = int(match.group(1))
    return {"command": command, "exit_code": exit_code, "output": output}


def _correctness_signals(response, validation: dict[str, Any] | None) -> dict[str, Any]:
    test_passed = validation is not None and validation.get("exit_code") == 0
    return {
        "completed": response.status == "completed",
        "has_diff": bool(response.diff and response.diff != "No working-tree changes"),
        "validation_passed": test_passed,
        "validation_available": validation is not None,
        "tool_calls": response.metrics.tool_calls,
        "total_tokens": response.metrics.total_tokens,
        "estimated_cost_usd": response.metrics.estimated_cost_usd,
    }


def _comparison(results: list[BenchmarkStrategyResult]) -> dict[str, Any]:
    by_strategy = {result.strategy: result for result in results}
    graph = by_strategy.get("graph_adaptive")
    scan = by_strategy.get("repo_scan")
    if graph is None or scan is None:
        return {"note": "Need both graph_adaptive and repo_scan to compute deltas."}

    graph_tokens = graph.response.metrics.total_tokens
    scan_tokens = scan.response.metrics.total_tokens
    token_delta = graph_tokens - scan_tokens
    token_delta_pct = None
    if scan_tokens:
        token_delta_pct = round(token_delta / scan_tokens * 100, 2)

    graph_cost = graph.response.metrics.estimated_cost_usd
    scan_cost = scan.response.metrics.estimated_cost_usd
    cost_delta = round(graph_cost - scan_cost, 8)
    return {
        "token_delta_graph_minus_scan": token_delta,
        "token_delta_pct": token_delta_pct,
        "cost_delta_graph_minus_scan_usd": cost_delta,
        "graph_completed": graph.correctness_signals.get("completed"),
        "repo_scan_completed": scan.correctness_signals.get("completed"),
        "graph_validation_passed": graph.correctness_signals.get("validation_passed"),
        "repo_scan_validation_passed": scan.correctness_signals.get("validation_passed"),
    }
