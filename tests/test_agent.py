import json
import subprocess
import tempfile
from pathlib import Path

from app.agent import CodingAgent
from app.models import AgentDecision


def test_agent_decision_schema_closes_tool_args_for_structured_outputs():
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(AgentDecision)
    args_schema = schema["$defs"]["AgentArgs"]

    assert args_schema["additionalProperties"] is False
    assert "query" in args_schema["properties"]
    assert "query" in args_schema["required"]


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def decide(self, task: str, history: str) -> AgentDecision:
        self.calls += 1
        if self.calls == 1:
            return AgentDecision(
                reasoning_summary="Inspect graph context narrowly first.",
                action="graph_query",
                args={"query": "hello", "depth": 1, "max_nodes": 20},
            )
        if self.calls == 2:
            return AgentDecision(
                reasoning_summary="Inspect the exact target file before editing.",
                action="read_file",
                args={"path": "hello.txt", "start_line": 1, "end_line": 20},
            )
        if self.calls == 3:
            return AgentDecision(
                reasoning_summary="Apply the requested minimal edit.",
                action="apply_patch",
                args={
                    "patch": "--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-hello\n+hello world\n"
                },
            )
        return AgentDecision(
            reasoning_summary="The requested edit is complete.",
            action="finish",
            args={},
            final_answer="Updated hello.txt.",
        )


def test_agent_can_use_graph_read_patch_and_finish():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "hello.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"],
            cwd=repo,
            check=True,
        )
        out = repo / "graphify-out"
        out.mkdir()
        (out / "graph.json").write_text(json.dumps({
            "nodes": [{"id": "hello", "label": "hello", "source_file": "hello.txt", "source_location": "L1"}],
            "links": [],
        }), encoding="utf-8")

        result = CodingAgent(llm=FakeLLM()).run(repo, "say hello world", max_steps=6, auto_index=False)
        assert result.status == "completed"
        assert "hello world" in (repo / "hello.txt").read_text(encoding="utf-8")
        assert "+hello world" in result.diff
        assert result.events[0].action == "graph_query"
        assert result.events[0].role == "planner"
        assert result.events[0].args["depth"] == 1


class AdaptiveDepthLLM:
    def __init__(self):
        self.calls = 0

    def decide(self, task: str, history: str) -> AgentDecision:
        self.calls += 1
        if self.calls == 1:
            return AgentDecision(
                reasoning_summary="Start with a bounded one-hop graph retrieval.",
                action="graph_query",
                args={"query": "OrderController", "depth": 1, "max_nodes": 20},
            )
        if self.calls == 2:
            assert '"recommended_next_depth": 2' in history
            return AgentDecision(
                reasoning_summary="The first graph slice is intentionally insufficient, so widen by one hop.",
                action="graph_query",
                args={"query": "OrderController", "depth": 2, "max_nodes": 20},
            )
        return AgentDecision(
            reasoning_summary="Graph traversal demonstrated the required dependency context.",
            action="finish",
            args={},
            final_answer="Adaptive BFS reached the needed context.",
        )


def test_agent_can_adapt_bfs_depth_from_diagnostics(monkeypatch):
    from app.config import settings
    old_threshold = settings.graph_sufficiency_threshold
    settings.graph_sufficiency_threshold = 1.1
    try:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "OrderController.java").write_text("class OrderController {}\n", encoding="utf-8")
            subprocess.run(["git", "add", "OrderController.java"], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"],
                cwd=repo,
                check=True,
            )
            out = repo / "graphify-out"
            out.mkdir()
            (out / "graph.json").write_text(json.dumps({
                "nodes": [
                    {"id": "OrderController", "label": "OrderController", "source_file": "OrderController.java"},
                    {"id": "OrderService", "label": "OrderService", "source_file": "OrderService.java"},
                    {"id": "OrderRepository", "label": "OrderRepository", "source_file": "OrderRepository.java"},
                ],
                "links": [
                    {"source": "OrderController", "target": "OrderService", "relation": "calls", "confidence": "EXTRACTED"},
                    {"source": "OrderService", "target": "OrderRepository", "relation": "calls", "confidence": "EXTRACTED"},
                ],
            }), encoding="utf-8")

            result = CodingAgent(llm=AdaptiveDepthLLM()).run(repo, "trace order flow", max_steps=5, auto_index=False)
            assert result.status == "completed"
            depths = [e.args.get("depth") for e in result.events if e.action == "graph_query"]
            assert depths == [1, 2]
    finally:
        settings.graph_sufficiency_threshold = old_threshold


class MeasuredLLM(FakeLLM):
    def consume_measurements(self):
        from app.metrics import LLMCallMetrics

        return [
            LLMCallMetrics(
                purpose="test",
                model="fake",
                input_tokens=100,
                output_tokens=25,
                total_tokens=125,
                estimated_cost_usd=0.01,
            )
        ]


def test_agent_can_run_in_isolated_worktree_and_report_metrics():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "demo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "hello.txt"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"],
            cwd=repo,
            check=True,
        )
        out = repo / "graphify-out"
        out.mkdir()
        (out / "graph.json").write_text(json.dumps({
            "nodes": [{"id": "hello", "label": "hello", "source_file": "hello.txt", "source_location": "L1"}],
            "links": [],
        }), encoding="utf-8")

        result = CodingAgent(llm=MeasuredLLM()).run(
            repo,
            "say hello world",
            max_steps=6,
            auto_index=False,
            isolated_worktree=True,
        )

        assert result.status == "completed"
        assert result.worktree is not None
        assert result.worktree["isolated"] is True
        active = Path(str(result.worktree["active_path"]))
        assert (repo / "hello.txt").read_text(encoding="utf-8") == "hello\n"
        assert (active / "hello.txt").read_text(encoding="utf-8") == "hello world\n"
        assert result.metrics.total_tokens == 125
        assert result.metrics.tool_calls >= 3
