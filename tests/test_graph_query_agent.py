import json
import subprocess
from pathlib import Path

from app.graph_query_agent import run_graph_query_agent
from app.models import GraphQueryDecision


def test_graph_query_agent_lets_llm_reformulate_and_answer(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    out = repo / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps({
        "nodes": [
            {"id": "OrderRepository", "label": "OrderRepository", "source_file": "src/OrderRepository.java"},
        ],
        "links": [],
    }), encoding="utf-8")

    class FakeLLM:
        def decide_graph_query_next(self, question, attempts, max_depth, max_iterations):
            if len(attempts) == 1:
                return GraphQueryDecision(
                    reasoning_summary="Initial seed had no matches, so try the repository symbol.",
                    action="query",
                    query="OrderRepository",
                    depth=0,
                )
            return GraphQueryDecision(
                reasoning_summary="The repository symbol was found.",
                action="answer",
                final_answer="The relevant symbol is OrderRepository in src/OrderRepository.java.",
            )

        def summarize_graph_query_attempts(self, query, attempts):
            raise AssertionError("final_answer should be used directly")

    monkeypatch.setattr("app.graph_query_agent.LLM", FakeLLM)

    result = run_graph_query_agent(
        repo,
        query="persistence layer",
        depth=0,
        max_nodes=20,
        relations=None,
        confidences=None,
        auto=True,
        max_iterations=3,
        max_depth=2,
    )

    assert result["answer"] == "The relevant symbol is OrderRepository in src/OrderRepository.java."
    assert result["answer_source"] == "llm"
    assert result["stop_reason"] == "llm_answer"
    assert [attempt["query"] for attempt in result["attempts"]] == ["persistence layer", "OrderRepository"]
