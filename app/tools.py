import os
import shlex
import subprocess
from pathlib import Path
from typing import Iterable

from .config import settings
from . import graphify_service


IGNORED_DIRS = {
    ".git", "graphify-out", "node_modules", "build", "dist", "target", ".gradle", ".idea", ".vscode",
    "coverage", ".next", ".venv", "venv", "__pycache__"
}

ALLOWED_EXECUTABLES = {
    "git", "pytest", "python", "python3", "mvn", "mvnw", "./mvnw", "gradle", "gradlew", "./gradlew",
    "npm", "pnpm", "yarn", "go", "cargo", "make", "rg"
}


def _truncate(text: str) -> str:
    limit = settings.max_tool_output_chars
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n... <truncated {len(text) - limit} chars> ...\n\n{tail}"


def safe_path(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    root = repo.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Path escapes repository: {relative}")
    return candidate


def repo_map(repo: Path, max_files: int = 500) -> str:
    files: list[str] = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        root_path = Path(root)
        for name in sorted(names):
            path = root_path / name
            rel = path.relative_to(repo).as_posix()
            files.append(rel)
            if len(files) >= max_files:
                return "\n".join(files) + f"\n... map capped at {max_files} files"
    return "\n".join(files)


def search(repo: Path, query: str, max_results: int = 100) -> str:
    if not query.strip():
        raise ValueError("query is required")
    cmd = ["rg", "-n", "--hidden", "--glob", "!.git/**"]
    for ignored in sorted(IGNORED_DIRS - {".git"}):
        cmd += ["--glob", f"!{ignored}/**"]
    cmd += ["--", query, "."]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=30)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "ripgrep failed")
    lines = proc.stdout.splitlines()[:max_results]
    return _truncate("\n".join(lines) if lines else "No matches")


def read_file(repo: Path, path: str, start_line: int = 1, end_line: int = 400) -> str:
    file = safe_path(repo, path)
    if not file.is_file():
        raise FileNotFoundError(path)
    if file.stat().st_size > 2_000_000:
        raise ValueError("Refusing to read file larger than 2 MB")
    raw = file.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    end = min(len(raw), max(start, end_line))
    numbered = [f"{i:5d} | {raw[i-1]}" for i in range(start, end + 1)]
    return _truncate("\n".join(numbered))


def _patch_paths(patch: str) -> Iterable[str]:
    for line in patch.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            value = line[4:].split("\t", 1)[0].strip()
            if value == "/dev/null":
                continue
            if value.startswith("a/") or value.startswith("b/"):
                value = value[2:]
            yield value


def apply_patch(repo: Path, patch: str) -> str:
    if not patch.strip():
        raise ValueError("patch is empty")
    paths = list(_patch_paths(patch))
    if not paths:
        raise ValueError("Patch contains no file paths")
    for path in paths:
        safe_path(repo, path)

    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=repo,
        input=patch,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check.returncode != 0:
        return "PATCH_CHECK_FAILED\n" + _truncate(check.stderr or check.stdout)

    apply = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=repo,
        input=patch,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if apply.returncode != 0:
        return "PATCH_APPLY_FAILED\n" + _truncate(apply.stderr or apply.stdout)
    return "Patch applied successfully. Note: the existing code graph is now potentially stale."


def _validate_command(command: str) -> list[str]:
    args = shlex.split(command)
    if not args:
        raise ValueError("command is empty")
    if settings.allow_arbitrary_commands:
        return args

    executable = args[0]
    base = os.path.basename(executable)
    if executable not in ALLOWED_EXECUTABLES and base not in ALLOWED_EXECUTABLES:
        raise ValueError(f"Command '{executable}' is not in the safe allowlist")

    dangerous = {"reset", "clean", "push", "checkout", "switch", "restore", "rm", "delete"}
    if base == "git" and any(token in dangerous for token in args[1:]):
        raise ValueError("Potentially destructive git command blocked")
    return args


def run_command(repo: Path, command: str) -> str:
    args = _validate_command(command)
    proc = subprocess.run(
        args,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=settings.command_timeout_seconds,
        env={**os.environ, "CI": "true"},
    )
    output = f"exit_code={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    return _truncate(output)


def git_diff(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--"], cwd=repo, capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        message = proc.stderr.strip() or "git diff failed"
        if "not a git repository" in message:
            return "No git repository; diff unavailable"
        raise RuntimeError(message)
    return _truncate(proc.stdout or "No working-tree changes")


def graph_build(repo: Path, force: bool = False) -> str:
    import json
    return _truncate(json.dumps(graphify_service.build_graph(repo, force=force), indent=2))


def graph_status(repo: Path) -> str:
    import json
    return _truncate(json.dumps(graphify_service.graph_status(repo), indent=2))


def graph_query(
    repo: Path,
    query: str,
    depth: int,
    max_nodes: int = 100,
    relations: list[str] | None = None,
    confidences: list[str] | None = None,
) -> str:
    return _truncate(graphify_service.graph_query(
        repo, query, depth, max_nodes=max_nodes, relations=relations, confidences=confidences
    ))


def graph_neighbors(repo: Path, node: str, depth: int = 1, max_nodes: int = 60) -> str:
    return _truncate(graphify_service.graph_neighbors(repo, node, depth, max_nodes))


def graph_path(repo: Path, source: str, target: str, max_hops: int = 8) -> str:
    return _truncate(graphify_service.graph_shortest_path(repo, source, target, max_hops))


def resolve_symbol(repo: Path, symbol: str, max_results: int = 20) -> str:
    from .symbol_resolver import resolve_symbol_json

    return _truncate(resolve_symbol_json(repo, symbol, max_results=max_results))
