from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .graph_answer import answer_graph_query, compact_graph_result
from .graphify_service import graph_query
from .llm import LLM
from .models import GraphQueryDecision


@dataclass(frozen=True)
class QueryParams:
    query: str
    depth: int
    max_nodes: int
    relations: tuple[str, ...] | None = None
    confidences: tuple[str, ...] | None = None

    def signature(self) -> tuple[Any, ...]:
        return (
            self.query,
            self.depth,
            self.max_nodes,
            self.relations or (),
            self.confidences or (),
        )


def run_graph_query_agent(
    repo: Path,
    *,
    query: str,
    depth: int,
    max_nodes: int,
    relations: list[str] | None,
    confidences: list[str] | None,
    auto: bool,
    max_iterations: int,
    max_depth: int | None,
) -> dict[str, Any]:
    limit_depth = min(max_depth if max_depth is not None else settings.graph_max_bfs_depth, settings.graph_max_bfs_depth)
    current = QueryParams(
        query=query,
        depth=max(0, min(depth, limit_depth)),
        max_nodes=max(1, min(max_nodes, 500)),
        relations=tuple(relations) if relations else None,
        confidences=tuple(confidences) if confidences else None,
    )
    iteration_limit = max_iterations if auto else 1
    llm = _load_llm() if auto else None
    llm_errors: list[str] = []
    if isinstance(llm, str):
        llm_errors.append(llm)
        llm = None

    seen: set[tuple[Any, ...]] = set()
    public_attempts: list[dict[str, Any]] = []
    llm_attempts: list[dict[str, Any]] = []
    final_result: dict[str, Any] | None = None
    final_answer: str | None = None
    answer_source: str | None = None
    stop_reason = "max_iterations"
    next_reason = "Initial graph query parameters."

    for step in range(1, iteration_limit + 1):
        seen.add(current.signature())
        result = json.loads(graph_query(
            repo,
            current.query,
            current.depth,
            max_nodes=current.max_nodes,
            relations=list(current.relations) if current.relations else None,
            confidences=list(current.confidences) if current.confidences else None,
        ))
        final_result = result

        public_attempt = _public_attempt(step, current, result, next_reason)
        public_attempts.append(public_attempt)
        llm_attempts.append({**public_attempt, "graph": compact_graph_result(result)})

        if not auto:
            stop_reason = "single_pass"
            break

        decision: GraphQueryDecision | None = None
        if llm is not None:
            try:
                decision = llm.decide_graph_query_next(query, llm_attempts, limit_depth, iteration_limit)
                public_attempt["decision"] = {
                    "action": decision.action,
                    "reasoning_summary": decision.reasoning_summary,
                }
                if decision.action == "answer":
                    final_answer = decision.final_answer
                    if not final_answer:
                        final_answer = llm.summarize_graph_query_attempts(query, llm_attempts)
                    answer_source = "llm"
                    stop_reason = "llm_answer"
                    break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                llm_errors.append(error)
                public_attempt["decision_error"] = error

        if step >= iteration_limit:
            break

        next_params = _next_params_from_decision(decision, current, limit_depth) if decision else None
        if next_params is None or next_params.signature() in seen:
            next_params = _heuristic_next_params(current, result, limit_depth)
            if next_params is not None and next_params.signature() in seen:
                next_params = None

        if next_params is None:
            stop_reason = "evidence_sufficient" if _is_sufficient(result) else "no_better_query"
            break

        next_reason = decision.reasoning_summary if decision else _heuristic_reason(current, next_params, result)
        current = next_params

    if final_result is None:
        raise RuntimeError("Graph query produced no result")

    if final_answer is None:
        final_answer, answer_source, answer_error = _summarize_final_answer(query, final_result, llm_attempts, llm)
        if answer_error:
            llm_errors.append(answer_error)

    response = {
        **final_result,
        "answer": final_answer,
        "answer_source": answer_source,
        "retrieval_mode": "auto" if auto else "single",
        "original_query": query,
        "attempts": public_attempts,
        "stop_reason": stop_reason,
        "max_iterations": iteration_limit,
        "max_depth": limit_depth,
    }
    if llm_errors:
        response["llm_error"] = "; ".join(llm_errors)
    return response


def _load_llm() -> LLM | str:
    try:
        return LLM()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def _public_attempt(step: int, params: QueryParams, result: dict[str, Any], reason: str) -> dict[str, Any]:
    diagnostics = result.get("diagnostics") or {}
    return {
        "step": step,
        "reasoning_summary": reason,
        "query": params.query,
        "depth": params.depth,
        "max_nodes": params.max_nodes,
        "relations": list(params.relations) if params.relations else None,
        "confidences": list(params.confidences) if params.confidences else None,
        "node_count": len(result.get("nodes") or []),
        "edge_count": len(result.get("edges") or []),
        "seed_nodes": result.get("seed_nodes") or [],
        "diagnostics": diagnostics,
    }


def _next_params_from_decision(
    decision: GraphQueryDecision | None,
    current: QueryParams,
    max_depth: int,
) -> QueryParams | None:
    if decision is None or decision.action != "query":
        return None
    return QueryParams(
        query=(decision.query or current.query).strip() or current.query,
        depth=max(0, min(decision.depth if decision.depth is not None else current.depth, max_depth)),
        max_nodes=max(1, min(decision.max_nodes if decision.max_nodes is not None else current.max_nodes, 500)),
        relations=tuple(decision.relations) if decision.relations is not None else current.relations,
        confidences=tuple(decision.confidences) if decision.confidences is not None else current.confidences,
    )


def _heuristic_next_params(current: QueryParams, result: dict[str, Any], max_depth: int) -> QueryParams | None:
    diagnostics = result.get("diagnostics") or {}
    node_count = len(result.get("nodes") or [])
    edge_count = len(result.get("edges") or [])

    if _is_sufficient(result):
        return None

    if (current.relations or current.confidences) and edge_count == 0:
        return QueryParams(current.query, current.depth, current.max_nodes, None, None)

    if node_count >= current.max_nodes and current.max_nodes < 500:
        return QueryParams(
            current.query,
            current.depth,
            min(500, max(current.max_nodes + 25, current.max_nodes * 2)),
            current.relations,
            current.confidences,
        )

    recommended = diagnostics.get("recommended_next_depth")
    if isinstance(recommended, int) and recommended > current.depth:
        return QueryParams(current.query, min(recommended, max_depth), current.max_nodes, current.relations, current.confidences)

    if node_count > 0 and current.depth < max_depth:
        return QueryParams(current.query, current.depth + 1, current.max_nodes, current.relations, current.confidences)

    return None


def _heuristic_reason(current: QueryParams, next_params: QueryParams, result: dict[str, Any]) -> str:
    if next_params.relations is None and next_params.confidences is None and (current.relations or current.confidences):
        return "Relax graph filters because the filtered traversal found too few relationships."
    if next_params.max_nodes > current.max_nodes:
        return "Increase max_nodes because the graph hit the current node cap."
    if next_params.depth > current.depth:
        recommended = (result.get("diagnostics") or {}).get("recommended_next_depth")
        if recommended == next_params.depth:
            return "Increase BFS depth based on graph diagnostics."
        return "Increase BFS depth to gather more dependency context."
    if next_params.query != current.query:
        return "Reformulate the seed query to improve lexical matching."
    return "Run another bounded graph query."


def _is_sufficient(result: dict[str, Any]) -> bool:
    return bool((result.get("diagnostics") or {}).get("sufficient"))


def _summarize_final_answer(
    query: str,
    final_result: dict[str, Any],
    attempts: list[dict[str, Any]],
    llm: LLM | None,
) -> tuple[str, str, str | None]:
    if llm is not None:
        try:
            answer = llm.summarize_graph_query_attempts(query, attempts)
            return answer, "llm", None
        except Exception as exc:
            llm_error = f"{type(exc).__name__}: {exc}"
            answer, source, fallback_error = answer_graph_query(query, final_result)
            return answer, source, "; ".join(e for e in [llm_error, fallback_error] if e)
    return answer_graph_query(query, final_result)
