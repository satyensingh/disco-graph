from app import graph_answer


def test_graph_answer_falls_back_when_llm_is_unavailable(monkeypatch):
    class BrokenLLM:
        def __init__(self):
            raise RuntimeError("missing key")

    monkeypatch.setattr(graph_answer, "LLM", BrokenLLM)

    answer, source, error = graph_answer.answer_graph_query("order flow", {
        "query": "order flow",
        "nodes": [
            {"id": "OrderController", "label": "OrderController", "source_file": "src/OrderController.java"},
            {"id": "OrderService", "label": "OrderService", "source_file": "src/OrderService.java"},
        ],
        "edges": [
            {"source": "OrderController", "target": "OrderService", "relation": "calls"},
        ],
        "diagnostics": {"sufficient": True, "reason": "retrieval passed"},
    })

    assert source == "fallback"
    assert "OrderController calls OrderService" in answer
    assert error == "RuntimeError: missing key"
