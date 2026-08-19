from __future__ import annotations

import hashlib
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeSession:
    original_repo: Path
    active_repo: Path
    isolated: bool
    branch: str | None = None
    commit: str | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "isolated": self.isolated,
            "original_path": str(self.original_repo),
            "active_path": str(self.active_repo),
            "branch": self.branch,
            "commit": self.commit,
            "reason": self.reason,
        }


def prepare_worktree(repo: Path, task: str, *, enabled: bool) -> WorktreeSession:
    repo = repo.resolve()
    if not enabled:
        return WorktreeSession(repo, repo, False, reason="isolation disabled for this run")
    if not _is_git_repo(repo):
        return WorktreeSession(repo, repo, False, reason="repository is not a Git checkout")

    commit = _git(repo, ["rev-parse", "HEAD"]).strip()
    branch = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    slug = _slug(task)
    digest = hashlib.sha256(f"{repo}:{commit}:{task}:{time.time_ns()}".encode()).hexdigest()[:10]
    worktree_root = repo.parent / ".disco-graph-worktrees"
    target = worktree_root / f"{repo.name}-{slug}-{digest}"
    if target.exists():
        return WorktreeSession(repo, target.resolve(), True, branch=branch, commit=commit, reason="reusing existing isolated worktree")

    worktree_root.mkdir(parents=True, exist_ok=True)
    _git(repo, ["worktree", "add", "--detach", str(target), commit])
    return WorktreeSession(repo, target.resolve(), True, branch=branch, commit=commit, reason="created detached isolated worktree")


def remove_worktree(session: WorktreeSession) -> None:
    if not session.isolated:
        return
    _git(session.original_repo, ["worktree", "remove", "--force", str(session.active_repo)])


def _is_git_repo(repo: Path) -> bool:
    proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo, capture_output=True, text=True, timeout=10)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git(repo: Path, args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.lower()).strip("-._")
    return (slug or "task")[:36]
