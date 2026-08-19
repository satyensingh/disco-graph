import json
from pathlib import Path

from app.code_graph import CodeGraph


def write_graph(path: Path) -> None:
    data = {
        "nodes": [
            {"id": "OrderController", "label": "OrderController", "source_file": "src/OrderController.java", "source_location": "L10"},
            {"id": "OrderService", "label": "OrderService", "source_file": "src/OrderService.java", "source_location": "L20"},
            {"id": "OrderRepository", "label": "OrderRepository", "source_file": "src/OrderRepository.java", "source_location": "L30"},
            {"id": "Database", "label": "Database", "source_file": "src/Database.java", "source_location": "L5"},
            {"id": "PaymentService", "label": "PaymentService", "source_file": "src/PaymentService.java", "source_location": "L12"},
        ],
        "links": [
            {"source": "OrderController", "target": "OrderService", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "OrderService", "target": "OrderRepository", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "OrderRepository", "target": "Database", "relation": "uses", "confidence": "INFERRED"},
            {"source": "OrderService", "target": "PaymentService", "relation": "calls", "confidence": "EXTRACTED"},
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def test_bfs_depth_expands_context(tmp_path: Path):
    path = tmp_path / "graph.json"
    write_graph(path)
    graph = CodeGraph.load(path)

    depth1 = graph.retrieve("OrderController", 1, seed_limit=1, sufficiency_threshold=1.1, max_depth=4)
    depth2 = graph.retrieve("OrderController", 2, seed_limit=1, sufficiency_threshold=1.1, max_depth=4)

    ids1 = {n["id"] for n in depth1["nodes"]}
    ids2 = {n["id"] for n in depth2["nodes"]}
    assert "OrderService" in ids1
    assert "OrderRepository" not in ids1
    assert "OrderRepository" in ids2
    assert len(ids2) > len(ids1)
    assert depth1["diagnostics"]["recommended_next_depth"] == 2


def test_confidence_filter_can_exclude_inferred_edges(tmp_path: Path):
    path = tmp_path / "graph.json"
    write_graph(path)
    graph = CodeGraph.load(path)

    result = graph.retrieve(
        "OrderController",
        3,
        seed_limit=1,
        confidences=["EXTRACTED"],
        sufficiency_threshold=0.0,
        max_depth=4,
    )
    ids = {n["id"] for n in result["nodes"]}
    assert "OrderRepository" in ids
    assert "Database" not in ids


def test_shortest_path_preserves_relations(tmp_path: Path):
    path = tmp_path / "graph.json"
    write_graph(path)
    graph = CodeGraph.load(path)

    result = graph.shortest_path("OrderController", "Database", max_hops=5)
    assert result["found"] is True
    assert result["hops"] == 3
    assert [n["id"] for n in result["nodes"]] == [
        "OrderController", "OrderService", "OrderRepository", "Database"
    ]
