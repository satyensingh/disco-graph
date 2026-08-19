from fastapi import FastAPI, HTTPException

from .agent import CodingAgent
from .benchmark import run_benchmark
from .config import settings
from .graphify_service import build_graph, graph_status
from .graph_query_agent import run_graph_query_agent
from .models import (
    AgentRunRequest,
    AgentRunResponse,
    BenchmarkRequest,
    BenchmarkResponse,
    CloneRequest,
    CloneResponse,
    GraphBuildRequest,
    GraphQueryRequest,
)
from .repo_manager import clone_repo, repo_path


app = FastAPI(title="disco-graph Coding Agent", version="3.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "3.0.0"}


@app.post("/repos/clone", response_model=CloneResponse)
def clone(request: CloneRequest) -> CloneResponse:
    try:
        rid, path = clone_repo(request.repo_url, request.branch, request.pat)
        return CloneResponse(repo_id=rid, path=str(path), branch=request.branch)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/repos/index")
def index_repo(request: GraphBuildRequest) -> dict:
    try:
        repo = repo_path(request.repo_id)
        return build_graph(repo, force=request.force)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/repos/{repo_id}/graph/status")
def get_graph_status(repo_id: str) -> dict:
    try:
        return graph_status(repo_path(repo_id))
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/repos/graph/query")
def query_graph(request: GraphQueryRequest) -> dict:
    try:
        repo = repo_path(request.repo_id)
        if request.depth > settings.graph_max_bfs_depth:
            raise ValueError(f"Requested BFS depth {request.depth} exceeds configured max {settings.graph_max_bfs_depth}")
        return run_graph_query_agent(
            repo,
            query=request.query,
            depth=request.depth,
            max_nodes=request.max_nodes,
            relations=request.relations,
            confidences=request.confidences,
            auto=request.auto,
            max_iterations=request.max_iterations,
            max_depth=request.max_depth,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/agent/run", response_model=AgentRunResponse)
def run_agent(request: AgentRunRequest) -> AgentRunResponse:
    try:
        repo = repo_path(request.repo_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    agent = CodingAgent()
    return agent.run(
        repo,
        request.task,
        request.max_steps,
        request.auto_index,
        request.isolated_worktree,
        request.retrieval_strategy,
    )


@app.post("/benchmarks/run", response_model=BenchmarkResponse)
def benchmark(request: BenchmarkRequest) -> BenchmarkResponse:
    try:
        repo = repo_path(request.repo_id)
        return run_benchmark(
            repo,
            repo_id=request.repo_id,
            task=request.task,
            strategies=list(dict.fromkeys(request.strategies)),
            max_steps=request.max_steps,
            validation_command=request.validation_command,
            auto_index=request.auto_index,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
