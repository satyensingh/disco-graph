from pathlib import Path
from typing import Any

from .config import settings
from .llm import LLM
from .metrics import RunMetrics, Timer
from .models import AgentRunResponse, ToolEvent
from .roles import build_roles, role_for_action, select_role
from . import tools
from .graphify_service import graph_available
from .worktrees import WorktreeSession, prepare_worktree, remove_worktree


class CodingAgent:
    def __init__(self, llm: LLM | None = None) -> None:
        self.llm = llm or LLM()
        self.roles = build_roles(self.llm)

    def _execute(self, repo: Path, action: str, args: dict[str, Any], retrieval_strategy: str) -> str:
        if retrieval_strategy == "repo_scan" and action.startswith("graph_"):
            return "TOOL_DISABLED: graph tools are disabled for the repo_scan baseline strategy."
        if action == "graph_status":
            return tools.graph_status(repo)
        if action == "graph_build":
            return tools.graph_build(repo, bool(args.get("force", False)))
        if action == "graph_query":
            depth = int(args.get("depth", settings.graph_default_bfs_depth))
            if depth > settings.graph_max_bfs_depth:
                raise ValueError(f"Requested BFS depth {depth} exceeds configured max {settings.graph_max_bfs_depth}")
            relations = args.get("relations")
            confidences = args.get("confidences")
            return tools.graph_query(
                repo,
                str(args.get("query", "")),
                depth,
                int(args.get("max_nodes", settings.graph_max_nodes)),
                [str(v) for v in relations] if isinstance(relations, list) else None,
                [str(v) for v in confidences] if isinstance(confidences, list) else None,
            )
        if action == "graph_neighbors":
            depth = int(args.get("depth", 1))
            if depth > settings.graph_max_bfs_depth:
                raise ValueError(f"Requested BFS depth {depth} exceeds configured max {settings.graph_max_bfs_depth}")
            return tools.graph_neighbors(
                repo,
                str(args.get("node", "")),
                depth,
                int(args.get("max_nodes", 60)),
            )
        if action == "graph_path":
            return tools.graph_path(
                repo,
                str(args.get("source", "")),
                str(args.get("target", "")),
                int(args.get("max_hops", 8)),
            )
        if action == "resolve_symbol":
            return tools.resolve_symbol(
                repo,
                str(args.get("symbol", "")),
                int(args.get("max_results", 20)),
            )
        if action == "repo_map":
            return tools.repo_map(repo, int(args.get("max_files", 500)))
        if action == "search":
            return tools.search(repo, str(args.get("query", "")), int(args.get("max_results", 100)))
        if action == "read_file":
            return tools.read_file(
                repo,
                str(args.get("path", "")),
                int(args.get("start_line", 1)),
                int(args.get("end_line", 400)),
            )
        if action == "apply_patch":
            return tools.apply_patch(repo, str(args.get("patch", "")))
        if action == "run_command":
            return tools.run_command(repo, str(args.get("command", "")))
        if action == "git_diff":
            return tools.git_diff(repo)
        raise ValueError(f"Unsupported action: {action}")

    @staticmethod
    def _history(events: list[ToolEvent]) -> str:
        chunks: list[str] = []
        for e in events:
            chunks.append(
                f"STEP {e.step}\nACTION: {e.action}\nWHY: {e.reasoning_summary}\nARGS: {e.args}\nRESULT:\n{e.result}"
            )
        return "\n\n".join(chunks)[-90000:]

    def run(
        self,
        repo: Path,
        task: str,
        max_steps: int | None = None,
        auto_index: bool | None = None,
        isolated_worktree: bool | None = None,
        retrieval_strategy: str = "graph_adaptive",
    ) -> AgentRunResponse:
        timer = Timer()
        metrics = RunMetrics()
        events: list[ToolEvent] = []
        limit = max_steps or settings.max_agent_steps
        use_auto_index = settings.auto_index_on_run if auto_index is None else auto_index
        use_isolated = settings.isolated_worktrees if isolated_worktree is None else isolated_worktree
        session: WorktreeSession | None = None
        active_repo = repo

        try:
            session = prepare_worktree(repo, task, enabled=use_isolated)
            active_repo = session.active_repo
            if use_auto_index and retrieval_strategy == "graph_adaptive" and not graph_available(active_repo):
                try:
                    result = tools.graph_build(active_repo, False)
                except Exception as exc:
                    result = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
                metrics.add_tool_result(result)
                events.append(
                    ToolEvent(
                        step=0,
                        action="graph_build",
                        role="system",
                        reasoning_summary="Bootstrap the persistent AST code graph so retrieval can start graph-first.",
                        args={"force": False},
                        result=result,
                    )
                )

            for step in range(1, limit + 1):
                role_name = select_role(events)
                decision = self.roles[role_name].decide(task, self._history(events), retrieval_strategy)
                event_role = role_for_action(decision.action, role_name)
                if decision.action == "finish":
                    diff = tools.git_diff(active_repo)
                    self._collect_llm_metrics(metrics)
                    metrics.elapsed_seconds = timer.elapsed()
                    return AgentRunResponse(
                        status="completed",
                        answer=decision.final_answer or "Task completed.",
                        events=events,
                        diff=diff,
                        metrics=metrics,
                        retrieval_strategy=retrieval_strategy,  # type: ignore[arg-type]
                        worktree=session.as_dict() if session else None,
                    )

                try:
                    result = self._execute(active_repo, decision.action, decision.args, retrieval_strategy)
                except Exception as exc:
                    result = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
                metrics.add_tool_result(result)

                events.append(
                    ToolEvent(
                        step=step,
                        action=decision.action,
                        role=event_role,
                        reasoning_summary=decision.reasoning_summary,
                        args=decision.args,
                        result=result,
                    )
                )

            self._collect_llm_metrics(metrics)
            metrics.elapsed_seconds = timer.elapsed()
            return AgentRunResponse(
                status="stopped",
                answer=f"Stopped after reaching the {limit}-step safety limit.",
                events=events,
                diff=tools.git_diff(active_repo),
                metrics=metrics,
                retrieval_strategy=retrieval_strategy,  # type: ignore[arg-type]
                worktree=session.as_dict() if session else None,
            )
        except Exception as exc:
            self._collect_llm_metrics(metrics)
            metrics.elapsed_seconds = timer.elapsed()
            return AgentRunResponse(
                status="failed",
                answer=f"Agent failed: {type(exc).__name__}: {exc}",
                events=events,
                diff=tools.git_diff(active_repo),
                metrics=metrics,
                retrieval_strategy=retrieval_strategy,  # type: ignore[arg-type]
                worktree=session.as_dict() if session else None,
            )
        finally:
            if session is not None and session.isolated and not settings.keep_isolated_worktrees:
                remove_worktree(session)

    def _collect_llm_metrics(self, metrics: RunMetrics) -> None:
        consume = getattr(self.llm, "consume_measurements", None)
        if callable(consume):
            metrics.add_llm_calls(consume())
