from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import AgentDecision, ToolEvent


class DecisionLLM(Protocol):
    def decide(self, task: str, history: str) -> AgentDecision:
        ...


@dataclass(frozen=True)
class RoleAgent:
    name: str
    directive: str
    llm: DecisionLLM

    def decide(self, task: str, history: str, retrieval_strategy: str) -> AgentDecision:
        role_task = (
            f"{self.directive}\n\n"
            f"Retrieval strategy for this run: {retrieval_strategy}.\n\n"
            f"Original task:\n{task}"
        )
        return self.llm.decide(role_task, history)


PLANNER = (
    "You are the Planner agent. Build the smallest useful evidence trail before edits. "
    "Prefer symbol resolution and bounded graph retrieval for unfamiliar code when graph retrieval is enabled."
)
CODER = (
    "You are the Coder agent. Make focused source changes only after inspecting exact files. "
    "Use unified diffs and keep edits limited to the task."
)
REVIEWER = (
    "You are the Reviewer agent. Validate the diff with targeted commands or source inspection, "
    "then finish only when the change is reasonably verified."
)


def build_roles(llm: DecisionLLM) -> dict[str, RoleAgent]:
    return {
        "planner": RoleAgent("planner", PLANNER, llm),
        "coder": RoleAgent("coder", CODER, llm),
        "reviewer": RoleAgent("reviewer", REVIEWER, llm),
    }


def select_role(events: list[ToolEvent]) -> str:
    if any(event.action == "apply_patch" and not event.result.startswith("TOOL_ERROR") for event in events):
        return "reviewer"
    if any(event.action in {"read_file", "resolve_symbol"} for event in events):
        return "coder"
    return "planner"


def role_for_action(action: str, default: str) -> str:
    if action == "apply_patch":
        return "coder"
    if action in {"run_command", "git_diff", "finish"} and default == "reviewer":
        return "reviewer"
    return default
