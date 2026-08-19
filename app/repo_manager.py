import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .config import ensure_workspace_root


def repo_path(repo_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", repo_id):
        raise ValueError("Invalid repo_id")
    root = ensure_workspace_root().resolve()
    path = (root / repo_id).resolve()
    if root not in path.parents:
        raise ValueError("Repository path escapes workspace")
    if not path.exists() or not (path / ".git").exists():
        raise FileNotFoundError(f"Unknown repository: {repo_id}")
    return path


def clone_repo(repo_url: str, branch: str, pat: str | None) -> tuple[str, Path]:
    rid = str(uuid.uuid4())
    root = ensure_workspace_root()
    target = root / rid
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    askpass_path: str | None = None
    try:
        if pat:
            fd, askpass_path = tempfile.mkstemp(prefix="git-askpass-", suffix=".sh")
            os.close(fd)
            Path(askpass_path).write_text(
                '#!/bin/sh\ncase "$1" in\n  *Username*) echo "$GIT_USERNAME" ;;\n  *) echo "$GIT_PASSWORD" ;;\nesac\n',
                encoding="utf-8",
            )
            os.chmod(askpass_path, 0o700)
            env.update(
                {
                    "GIT_ASKPASS": askpass_path,
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_USERNAME": "x-access-token",
                    "GIT_PASSWORD": pat,
                }
            )

        cmd = ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(target)]
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git clone failed")
        return rid, target
    finally:
        if askpass_path:
            Path(askpass_path).unlink(missing_ok=True)
