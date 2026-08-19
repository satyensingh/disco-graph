from typing import Any, Literal
from pydantic import BaseModel, Field

from .metrics import RunMetrics


class CloneRequest(BaseModel):
    repo_url: str
    branch: str = "main"
    pat: str | None = Field(default=None, description="Optional Git PAT; never persisted by the service")


class CloneResponse(BaseModel):
    repo_id: str
    path: str
    branch: str


class GraphBuildRequest(BaseModel):
    repo_id: str
    force: bool = False


class GraphQueryRequest(BaseModel):
    repo_id: str
    query: str
    depth: int = Field(default=1, ge=0, le=8, description="Starting BFS depth when auto is true.")
    max_nodes: int = Field(default=100, ge=1, le=500)
    relations: list[str] | None = None
    confidences: list[Literal["EXTRACTED", "INFERRED", "AMBIGUOUS", "UNKNOWN"]] | None = None
    auto: bool = Field(
        default=True,
        description="Let the endpoint refine query parameters and BFS depth before answering.",
    )
    max_iterations: int = Field(default=4, ge=1, le=8)
    max_depth: int | None = Field(
        default=None,
        ge=0,
        le=8,
        description="Maximum BFS depth for autonomous retrieval. Defaults to configured graph max depth.",
    )


class GraphQueryDecision(BaseModel):
    reasoning_summary: str = Field(
        description="A short user-safe explanation for the next graph query choice."
    )
    action: Literal["answer", "query"]
    query: str | None = Field(
        default=None,
        description="Next graph seed query when action is query. Reuse the original when only changing depth.",
    )
    depth: int | None = Field(default=None, ge=0, le=8)
    max_nodes: int | None = Field(default=None, ge=1, le=500)
    relations: list[str] | None = None
    confidences: list[Literal["EXTRACTED", "INFERRED", "AMBIGUOUS", "UNKNOWN"]] | None = None
    final_answer: str | None = Field(default=None, description="Plain-English answer when action is answer.")


class AgentRunRequest(BaseModel):
    repo_id: str
    task: str
    max_steps: int | None = Field(default=None, ge=1, le=60)
    auto_index: bool | None = Field(
        default=None,
        description="Build the Graphify AST graph automatically when missing. Defaults to AUTO_INDEX_ON_RUN.",
    )
    isolated_worktree: bool | None = Field(
        default=None,
        description="Run inside a detached Git worktree. Defaults to ISOLATED_WORKTREES.",
    )
    retrieval_strategy: Literal["graph_adaptive", "repo_scan"] = Field(
        default="graph_adaptive",
        description="Use graph/adaptive BFS retrieval or disable graph tools for a repo-scanning baseline.",
    )


class BenchmarkRequest(BaseModel):
    repo_id: str
    task: str
    max_steps: int | None = Field(default=None, ge=1, le=60)
    strategies: list[Literal["graph_adaptive", "repo_scan"]] = Field(
        default_factory=lambda: ["graph_adaptive", "repo_scan"]
    )
    validation_command: str | None = Field(
        default=None,
        description="Optional allow-listed command to run after each strategy for a correctness signal.",
    )
    auto_index: bool = True


class ToolEvent(BaseModel):
    step: int
    action: str
    reasoning_summary: str
    role: Literal["system", "planner", "coder", "reviewer"] = "planner"
    args: dict[str, Any] = Field(default_factory=dict)
    result: str


class AgentRunResponse(BaseModel):
    status: Literal["completed", "stopped", "failed"]
    answer: str
    events: list[ToolEvent]
    diff: str
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    retrieval_strategy: Literal["graph_adaptive", "repo_scan"] = "graph_adaptive"
    worktree: dict[str, Any] | None = None


class AgentDecision(BaseModel):
    reasoning_summary: str = Field(
        description="A short user-safe explanation of why this next action is useful. Do not reveal private chain-of-thought."
    )
    action: Literal[
        "graph_status",
        "graph_build",
        "graph_query",
        "graph_neighbors",
        "graph_path",
        "resolve_symbol",
        "repo_map",
        "search",
        "read_file",
        "apply_patch",
        "run_command",
        "git_diff",
        "finish",
    ]
    args: dict[str, Any] = Field(default_factory=dict)
    final_answer: str | None = None


class BenchmarkStrategyResult(BaseModel):
    strategy: Literal["graph_adaptive", "repo_scan"]
    response: AgentRunResponse
    validation: dict[str, Any] | None = None
    correctness_signals: dict[str, Any] = Field(default_factory=dict)


class BenchmarkResponse(BaseModel):
    repo_id: str
    task: str
    results: list[BenchmarkStrategyResult]
    comparison: dict[str, Any]
