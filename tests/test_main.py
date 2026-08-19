import json
import subprocess
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_clone_endpoint_generates_repo_id_without_request_repo_id(monkeypatch, tmp_path: Path):
    generated_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.main.clone_repo",
        lambda repo_url, branch, pat: (generated_id, tmp_path / generated_id),
    )

    response = TestClient(app).post("/repos/clone", json={
        "repo_url": "https://github.com/example/project.git",
        "branch": "main",
    })

    assert response.status_code == 200
    assert response.json() == {
        "repo_id": generated_id,
        "path": str(tmp_path / generated_id),
        "branch": "main",
    }


def test_graph_query_endpoint_returns_plain_english_answer(monkeypatch, tmp_path: Path):
    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    out = repo / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps({
        "nodes": [
            {"id": "OrderController", "label": "OrderController", "source_file": "src/OrderController.java"},
            {"id": "OrderService", "label": "OrderService", "source_file": "src/OrderService.java"},
        ],
        "links": [
            {"source": "OrderController", "target": "OrderService", "relation": "calls", "confidence": "EXTRACTED"},
        ],
    }), encoding="utf-8")

    captured = {}

    def fake_answer(query, graph_result):
        captured["query"] = query
        captured["graph_result"] = graph_result
        return "OrderController calls OrderService.", "llm", None

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    monkeypatch.setattr("app.graph_query_agent.answer_graph_query", fake_answer)

    response = TestClient(app).post("/repos/graph/query", json={
        "repo_id": "demo",
        "query": "order flow",
        "depth": 1,
        "max_nodes": 20,
        "auto": False,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "OrderController calls OrderService."
    assert body["answer_source"] == "llm"
    assert body["nodes"][0]["id"] == "OrderController"
    assert body["retrieval_mode"] == "single"
    assert len(body["attempts"]) == 1
    assert captured["query"] == "order flow"
    assert captured["graph_result"]["edges"][0]["relation"] == "calls"


def test_graph_query_endpoint_auto_expands_depth_without_llm(monkeypatch, tmp_path: Path):
    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    out = repo / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps({
        "nodes": [
            {"id": "OrderController", "label": "OrderController", "source_file": "src/OrderController.java"},
            {"id": "OrderService", "label": "OrderService", "source_file": "src/OrderService.java"},
            {"id": "OrderRepository", "label": "OrderRepository", "source_file": "src/OrderRepository.java"},
        ],
        "links": [
            {"source": "OrderController", "target": "OrderService", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "OrderService", "target": "OrderRepository", "relation": "calls", "confidence": "EXTRACTED"},
        ],
    }), encoding="utf-8")

    class BrokenLLM:
        def __init__(self):
            raise RuntimeError("missing key")

    def fake_answer(query, graph_result):
        return "OrderController reaches OrderRepository through OrderService.", "fallback", None

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    monkeypatch.setattr("app.graph_query_agent.LLM", BrokenLLM)
    monkeypatch.setattr("app.graph_query_agent.answer_graph_query", fake_answer)

    response = TestClient(app).post("/repos/graph/query", json={
        "repo_id": "demo",
        "query": "OrderController persistence database transaction",
        "depth": 1,
        "max_nodes": 20,
        "max_iterations": 2,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["retrieval_mode"] == "auto"
    assert [attempt["depth"] for attempt in body["attempts"]] == [1, 2]
    assert body["query"] == "OrderController persistence database transaction"
    assert body["depth"] == 2
    assert "OrderRepository" in {node["id"] for node in body["nodes"]}
    assert body["stop_reason"] in {"evidence_sufficient", "no_better_query", "max_iterations"}
