from __future__ import annotations

import json
import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i", "in", "into",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "what", "when", "where", "which",
    "who", "why", "with", "fix", "change", "code", "file", "please", "should", "can", "does", "do",
}


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_.:$-]*", text):
        candidates = [raw.lower()]
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
        candidates.extend(part.lower() for part in re.split(r"[^A-Za-z0-9]+", spaced) if part)
        for token in candidates:
            if token and token not in STOPWORDS:
                tokens.append(token)
    return tokens


def _as_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("name") or value)
    return str(value)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str
    confidence: str
    attrs: dict[str, Any]


class CodeGraph:
    """Small, dependency-free reader/traverser for Graphify's graph.json.

    Graphify has used NetworkX node-link JSON as well as extractor-shaped JSON.
    This loader intentionally accepts both `links` and `edges`.
    """

    def __init__(self, nodes: dict[str, dict[str, Any]], edges: list[Edge]) -> None:
        self.nodes = nodes
        self.edges = edges
        self.adj: dict[str, list[Edge]] = defaultdict(list)
        for edge in edges:
            self.adj[edge.source].append(edge)
            # Context retrieval is intentionally bidirectional. Direction is preserved on Edge.
            self.adj[edge.target].append(edge)

    @classmethod
    def load(cls, path: Path) -> "CodeGraph":
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_nodes = data.get("nodes", [])
        nodes: dict[str, dict[str, Any]] = {}
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                raw = {"id": str(raw), "label": str(raw)}
            node_id = str(raw.get("id") or raw.get("name") or raw.get("label"))
            if not node_id or node_id == "None":
                continue
            nodes[node_id] = {**raw, "id": node_id}

        raw_edges = data.get("links") or data.get("edges") or []
        edges: list[Edge] = []
        for raw in raw_edges:
            if not isinstance(raw, dict):
                continue
            source = _as_id(raw.get("source"))
            target = _as_id(raw.get("target"))
            if source not in nodes or target not in nodes:
                continue
            relation = str(raw.get("relation") or raw.get("type") or raw.get("label") or "related")
            confidence = str(raw.get("confidence") or "UNKNOWN").upper()
            edges.append(Edge(source, target, relation, confidence, dict(raw)))
        return cls(nodes, edges)

    def summary(self) -> dict[str, Any]:
        confidence_counts: dict[str, int] = defaultdict(int)
        relation_counts: dict[str, int] = defaultdict(int)
        for edge in self.edges:
            confidence_counts[edge.confidence] += 1
            relation_counts[edge.relation] += 1
        files = {str(n.get("source_file")) for n in self.nodes.values() if n.get("source_file")}
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "source_files": len(files),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "top_relations": sorted(relation_counts.items(), key=lambda x: (-x[1], x[0]))[:12],
        }

    def _node_text(self, node: dict[str, Any]) -> str:
        parts = [
            str(node.get("id", "")),
            str(node.get("label", "")),
            str(node.get("name", "")),
            str(node.get("qualified_name", "")),
            str(node.get("source_file", "")),
            str(node.get("type", "")),
            str(node.get("kind", "")),
        ]
        return " ".join(parts)

    def ranked_seeds(self, query: str, limit: int = 8) -> list[tuple[str, float]]:
        query_tokens = _tokens(query)
        query_lower = query.lower().strip()
        scored: list[tuple[str, float]] = []
        for node_id, node in self.nodes.items():
            text = self._node_text(node)
            text_lower = text.lower()
            node_tokens = set(_tokens(text))
            overlap = sum(1 for token in query_tokens if token in node_tokens or token in text_lower)
            exact_bonus = 3.0 if query_lower and query_lower in text_lower else 0.0
            label = str(node.get("label") or node.get("name") or node_id).lower()
            label_bonus = sum(1.5 for token in query_tokens if token == label or token in label)
            path_bonus = sum(0.35 for token in query_tokens if token in str(node.get("source_file", "")).lower())
            degree_bonus = min(1.0, math.log2(len(self.adj.get(node_id, [])) + 1) / 6)
            score = overlap + exact_bonus + label_bonus + path_bonus + degree_bonus
            if score > 0:
                scored.append((node_id, round(score, 4)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:limit]

    def _edge_allowed(
        self,
        edge: Edge,
        relations: set[str] | None,
        confidences: set[str] | None,
    ) -> bool:
        if relations and edge.relation.lower() not in relations:
            return False
        if confidences and edge.confidence.upper() not in confidences:
            return False
        return True

    def retrieve(
        self,
        query: str,
        depth: int,
        *,
        max_nodes: int = 100,
        seed_limit: int = 8,
        relations: Iterable[str] | None = None,
        confidences: Iterable[str] | None = None,
        sufficiency_threshold: float = 0.68,
        max_depth: int = 5,
    ) -> dict[str, Any]:
        depth = max(0, min(depth, max_depth))
        relation_set = {r.lower() for r in relations or []} or None
        confidence_set = {c.upper() for c in confidences or []} or None
        seeds = self.ranked_seeds(query, seed_limit)
        if not seeds:
            return {
                "query": query,
                "depth": depth,
                "seed_nodes": [],
                "nodes": [],
                "edges": [],
                "diagnostics": {
                    "sufficiency_score": 0.0,
                    "query_token_coverage": 0.0,
                    "source_file_count": 0,
                    "node_count": 0,
                    "edge_count": 0,
                    "sufficient": False,
                    "recommended_next_depth": min(max_depth, depth + 1) if depth < max_depth else None,
                    "reason": "No graph nodes lexically matched the query; use repository search or a different graph query.",
                },
            }

        queue: deque[tuple[str, int]] = deque((node_id, 0) for node_id, _ in seeds)
        distance: dict[str, int] = {node_id: 0 for node_id, _ in seeds}
        visited_order: list[str] = []

        while queue and len(distance) <= max_nodes:
            node_id, d = queue.popleft()
            if node_id not in visited_order:
                visited_order.append(node_id)
            if d >= depth:
                continue
            for edge in self.adj.get(node_id, []):
                if not self._edge_allowed(edge, relation_set, confidence_set):
                    continue
                other = edge.target if edge.source == node_id else edge.source
                if other not in distance and len(distance) < max_nodes:
                    distance[other] = d + 1
                    queue.append((other, d + 1))

        selected = set(distance)
        selected_edges = [
            edge for edge in self.edges
            if edge.source in selected and edge.target in selected and self._edge_allowed(edge, relation_set, confidence_set)
        ]

        query_tokens = set(_tokens(query))
        context_tokens: set[str] = set()
        source_files: set[str] = set()
        nodes_out: list[dict[str, Any]] = []
        for node_id in sorted(selected, key=lambda n: (distance[n], -dict(seeds).get(n, 0), n)):
            node = self.nodes[node_id]
            label = str(node.get("label") or node.get("name") or node_id)
            source_file = node.get("source_file")
            if source_file:
                source_files.add(str(source_file))
            context_tokens.update(_tokens(self._node_text(node)))
            nodes_out.append(
                {
                    "id": node_id,
                    "label": label,
                    "type": node.get("type") or node.get("kind"),
                    "source_file": source_file,
                    "source_location": node.get("source_location") or node.get("location"),
                    "community": node.get("community"),
                    "distance": distance[node_id],
                }
            )

        edge_out = [
            {
                "source": edge.source,
                "target": edge.target,
                "relation": edge.relation,
                "confidence": edge.confidence,
            }
            for edge in selected_edges
        ]

        coverage = 1.0 if not query_tokens else len(query_tokens & context_tokens) / len(query_tokens)
        file_factor = min(1.0, len(source_files) / 4.0)
        connection_factor = min(1.0, len(selected_edges) / max(1.0, len(selected)))
        seed_strength = min(1.0, (seeds[0][1] if seeds else 0.0) / max(4.0, len(query_tokens) * 1.5))
        sufficiency = round(0.50 * coverage + 0.20 * file_factor + 0.15 * connection_factor + 0.15 * seed_strength, 3)
        sufficient = sufficiency >= sufficiency_threshold
        recommended = None if sufficient or depth >= max_depth else depth + 1

        reason_parts = [
            f"query token coverage={coverage:.2f}",
            f"files={len(source_files)}",
            f"connected edges={len(selected_edges)}",
        ]
        if sufficient:
            reason_parts.append("retrieval passed the configured evidence threshold")
        elif recommended is not None:
            reason_parts.append(f"evidence is still thin; depth {recommended} is the next bounded expansion")
        else:
            reason_parts.append("maximum BFS depth reached; use lexical search/read_file if evidence is still missing")

        return {
            "query": query,
            "depth": depth,
            "seed_nodes": [{"id": node_id, "score": score} for node_id, score in seeds],
            "nodes": nodes_out,
            "edges": edge_out,
            "diagnostics": {
                "sufficiency_score": sufficiency,
                "query_token_coverage": round(coverage, 3),
                "source_file_count": len(source_files),
                "node_count": len(nodes_out),
                "edge_count": len(edge_out),
                "sufficient": sufficient,
                "recommended_next_depth": recommended,
                "reason": "; ".join(reason_parts),
            },
        }

    def resolve_node(self, value: str) -> str | None:
        if value in self.nodes:
            return value
        needle = value.lower().strip()
        exact_labels = [
            node_id for node_id, node in self.nodes.items()
            if str(node.get("label") or node.get("name") or "").lower() == needle
        ]
        if exact_labels:
            return sorted(exact_labels)[0]
        partial = [
            node_id for node_id, node in self.nodes.items()
            if needle in self._node_text(node).lower()
        ]
        if partial:
            partial.sort(key=lambda n: (-len(self.adj.get(n, [])), n))
            return partial[0]
        return None

    def neighbors(self, node: str, depth: int = 1, max_nodes: int = 60) -> dict[str, Any]:
        resolved = self.resolve_node(node)
        if resolved is None:
            return {"error": f"Node not found: {node}"}
        result = self.retrieve(
            str(self.nodes[resolved].get("label") or resolved),
            depth,
            max_nodes=max_nodes,
            seed_limit=1,
            sufficiency_threshold=0.0,
            max_depth=max(depth, 1),
        )
        result["resolved_node"] = resolved
        return result

    def shortest_path(self, source: str, target: str, max_hops: int = 8) -> dict[str, Any]:
        start = self.resolve_node(source)
        end = self.resolve_node(target)
        if start is None or end is None:
            return {"error": "Could not resolve source or target", "source": start, "target": end}
        queue = deque([start])
        parent: dict[str, tuple[str, Edge] | None] = {start: None}
        depth = {start: 0}
        while queue:
            current = queue.popleft()
            if current == end:
                break
            if depth[current] >= max_hops:
                continue
            for edge in self.adj.get(current, []):
                other = edge.target if edge.source == current else edge.source
                if other in parent:
                    continue
                parent[other] = (current, edge)
                depth[other] = depth[current] + 1
                queue.append(other)
        if end not in parent:
            return {"found": False, "source": start, "target": end, "max_hops": max_hops}

        node_path = [end]
        edge_path: list[dict[str, Any]] = []
        cursor = end
        while parent[cursor] is not None:
            prev, edge = parent[cursor]  # type: ignore[misc]
            edge_path.append({
                "source": edge.source,
                "target": edge.target,
                "relation": edge.relation,
                "confidence": edge.confidence,
            })
            cursor = prev
            node_path.append(cursor)
        node_path.reverse()
        edge_path.reverse()
        return {
            "found": True,
            "hops": len(edge_path),
            "nodes": [
                {
                    "id": node_id,
                    "label": self.nodes[node_id].get("label") or node_id,
                    "source_file": self.nodes[node_id].get("source_file"),
                    "source_location": self.nodes[node_id].get("source_location"),
                }
                for node_id in node_path
            ],
            "edges": edge_path,
        }
