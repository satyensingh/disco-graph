from __future__ import annotations

from typing import Any

from .llm import LLM


MAX_LLM_NODES = 80
MAX_LLM_EDGES = 120


def answer_graph_query(query: str, graph_result: dict[str, Any]) -> tuple[str, str, str | None]:
    llm_error: str | None = None
    try:
        answer = LLM().summarize_graph_query(query, compact_graph_result(graph_result))
        if answer:
            return answer, "llm", None
    except Exception as exc:
        llm_error = f"{type(exc).__name__}: {exc}"

    return _fallback_answer(graph_result), "fallback", llm_error


def compact_graph_result(graph_result: dict[str, Any]) -> dict[str, Any]:
    nodes = list(graph_result.get("nodes") or [])
    edges = list(graph_result.get("edges") or [])
    compact = dict(graph_result)
    compact["nodes"] = nodes[:MAX_LLM_NODES]
    compact["edges"] = edges[:MAX_LLM_EDGES]
    compact["truncated_for_llm"] = {
        "nodes_included": min(len(nodes), MAX_LLM_NODES),
        "nodes_total": len(nodes),
        "edges_included": min(len(edges), MAX_LLM_EDGES),
        "edges_total": len(edges),
    }
    return compact


def _fallback_answer(graph_result: dict[str, Any]) -> str:
    query = str(graph_result.get("query") or "the query")
    diagnostics = graph_result.get("diagnostics") or {}
    nodes = list(graph_result.get("nodes") or [])
    edges = list(graph_result.get("edges") or [])
    if not nodes:
        reason = diagnostics.get("reason") or "No matching graph evidence was found."
        return f"I could not find matching graph nodes for '{query}'. {reason}"

    labels_by_id = {
        str(node.get("id")): str(node.get("label") or node.get("id"))
        for node in nodes
        if node.get("id") is not None
    }
    primary = ", ".join(_node_label(node) for node in nodes[:5])
    files = sorted({str(node.get("source_file")) for node in nodes if node.get("source_file")})
    file_text = ", ".join(files[:5])
    if len(files) > 5:
        file_text += f", and {len(files) - 5} more"

    relationships = []
    for edge in edges[:6]:
        source = labels_by_id.get(str(edge.get("source")), str(edge.get("source")))
        target = labels_by_id.get(str(edge.get("target")), str(edge.get("target")))
        relation = edge.get("relation") or "relates to"
        relationships.append(f"{source} {relation} {target}")

    parts = [f"For '{query}', the graph found {len(nodes)} relevant node(s), led by {primary}."]
    if relationships:
        parts.append("Key relationships include " + "; ".join(relationships) + ".")
    if file_text:
        parts.append(f"The main source file(s) are {file_text}.")

    reason = diagnostics.get("reason")
    if reason:
        parts.append(str(reason))
    if diagnostics.get("sufficient") is False and diagnostics.get("recommended_next_depth") is not None:
        parts.append(f"The graph evidence is still thin; try depth {diagnostics['recommended_next_depth']} for more context.")

    return " ".join(parts)


def _node_label(node: dict[str, Any]) -> str:
    label = node.get("label") or node.get("id") or "unknown"
    source = node.get("source_file")
    if source:
        return f"{label} ({source})"
    return str(label)
