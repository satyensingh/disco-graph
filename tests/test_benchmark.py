import subprocess
from pathlib import Path

from app.benchmark import run_benchmark
from app.metrics import RunMetrics
from app.models import AgentRunResponse


class FakeAgent:
    def run(self, repo, task, max_steps=None, auto_index=None, isolated_worktree=None, retrieval_strategy="graph_adaptive"):
        metrics = RunMetrics(
            total_tokens=80 if retrieval_strategy == "graph_adaptive" else 120,
            estimated_cost_usd=0.008 if retrieval_strategy == "graph_adaptive" else 0.012,
            tool_calls=3,
        )
        return AgentRunResponse(
            status="completed",
            answer=f"{retrieval_strategy} done",
            events=[],
            diff="diff --git a/a b/a\n",
            metrics=metrics,
            retrieval_strategy=retrieval_strategy,
            worktree={"active_path": str(repo), "isolated": True},
        )


def test_benchmark_compares_graph_and_repo_scan(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    monkeypatch.setattr("app.benchmark.CodingAgent", FakeAgent)

    result = run_benchmark(
        repo,
        repo_id="repo",
        task="fix bug",
        strategies=["graph_adaptive", "repo_scan"],
        max_steps=3,
        validation_command=None,
        auto_index=True,
    )

    assert [item.strategy for item in result.results] == ["graph_adaptive", "repo_scan"]
    assert result.comparison["token_delta_graph_minus_scan"] == -40
    assert result.comparison["cost_delta_graph_minus_scan_usd"] == -0.004
