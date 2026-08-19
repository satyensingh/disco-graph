from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import graphify_service


IGNORED_DIRS = {
    ".git", "graphify-out", "node_modules", "build", "dist", "target", ".gradle", ".idea", ".vscode",
    "coverage", ".next", ".venv", "venv", "__pycache__"
}


LANGUAGE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".kt", ".kts"
}


@dataclass(frozen=True)
class SymbolCandidate:
    name: str
    kind: str
    path: str
    line: int
    column: int = 1
    detail: str = ""
    source: str = "static"
    confidence: str = "medium"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "detail": self.detail,
            "source": self.source,
            "confidence": self.confidence,
        }


def resolve_symbol(repo: Path, symbol: str, max_results: int = 20) -> dict[str, object]:
    symbol = symbol.strip()
    if not symbol:
        raise ValueError("symbol is required")

    candidates: list[SymbolCandidate] = []
    candidates.extend(_graph_candidates(repo, symbol))
    candidates.extend(_python_candidates(repo, symbol))
    candidates.extend(_regex_candidates(repo, symbol))
    candidates.extend(_compiler_candidates(repo, symbol))

    deduped = _dedupe(candidates)
    ranked = sorted(deduped, key=lambda c: (_rank(c, symbol), c.path, c.line))
    return {
        "symbol": symbol,
        "resolver": "graph+lsp-compatible-static+compiler-probes",
        "candidates": [candidate.as_dict() for candidate in ranked[:max_results]],
        "diagnostics": {
            "candidate_count": len(deduped),
            "truncated": len(deduped) > max_results,
            "sources": sorted({candidate.source for candidate in deduped}),
        },
    }


def _walk_source_files(repo: Path) -> Iterable[Path]:
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
        root_path = Path(root)
        for name in sorted(files):
            path = root_path / name
            if path.suffix in LANGUAGE_SUFFIXES and path.stat().st_size <= 2_000_000:
                yield path


def _graph_candidates(repo: Path, symbol: str) -> list[SymbolCandidate]:
    path = graphify_service.graph_path(repo)
    if not path.exists():
        return []
    try:
        graph = graphify_service.load_graph(repo)
    except Exception:
        return []

    needle = symbol.lower()
    out: list[SymbolCandidate] = []
    for node_id, node in graph.nodes.items():
        label = str(node.get("label") or node.get("name") or node_id)
        qualified = str(node.get("qualified_name") or node_id)
        if needle not in label.lower() and needle not in qualified.lower():
            continue
        source_file = node.get("source_file")
        if not source_file:
            continue
        line = _line_from_location(node.get("source_location") or node.get("location"))
        out.append(
            SymbolCandidate(
                name=label,
                kind=str(node.get("type") or node.get("kind") or "symbol"),
                path=str(source_file),
                line=line,
                detail=qualified,
                source="graphify",
                confidence="high" if label.lower() == needle else "medium",
            )
        )
    return out


def _python_candidates(repo: Path, symbol: str) -> list[SymbolCandidate]:
    out: list[SymbolCandidate] = []
    for path in _walk_source_files(repo):
        if path.suffix not in {".py", ".pyi"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = path.relative_to(repo).as_posix()
        for node in ast.walk(tree):
            name = getattr(node, "name", None)
            if name != symbol:
                continue
            if isinstance(node, ast.ClassDef):
                kind = "class"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "function"
            else:
                kind = "symbol"
            out.append(
                SymbolCandidate(
                    name=symbol,
                    kind=kind,
                    path=rel,
                    line=int(getattr(node, "lineno", 1)),
                    column=int(getattr(node, "col_offset", 0)) + 1,
                    detail=f"python ast {kind}",
                    source="python-ast",
                    confidence="high",
                )
            )
    return out


def _regex_candidates(repo: Path, symbol: str) -> list[SymbolCandidate]:
    escaped = re.escape(symbol)
    patterns = {
        "class": re.compile(rf"\b(?:export\s+)?(?:abstract\s+)?(?:class|interface|enum|record)\s+{escaped}\b"),
        "function": re.compile(rf"\b(?:export\s+)?(?:async\s+)?function\s+{escaped}\b|\b{escaped}\s*\([^)]*\)\s*(?::[^{chr(123)}]+)?{chr(123)}"),
        "value": re.compile(rf"\b(?:export\s+)?(?:const|let|var|type)\s+{escaped}\b"),
        "method": re.compile(rf"\b(?:public|private|protected|static|final|async|\s)+[\w<>\[\], ?]+\s+{escaped}\s*\("),
    }
    out: list[SymbolCandidate] = []
    for path in _walk_source_files(repo):
        if path.suffix in {".py", ".pyi"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(repo).as_posix()
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            for kind, pattern in patterns.items():
                match = pattern.search(stripped)
                if not match:
                    continue
                out.append(
                    SymbolCandidate(
                        name=symbol,
                        kind=kind,
                        path=rel,
                        line=number,
                        column=max(1, line.find(symbol) + 1),
                        detail=stripped[:240],
                        source="static-pattern",
                        confidence="medium",
                    )
                )
                break
    return out


def _compiler_candidates(repo: Path, symbol: str) -> list[SymbolCandidate]:
    """Ask cheap project-aware tools for references when they are available.

    This keeps the resolver LSP/compiler aware without requiring a language server in
    the base image. The static resolvers above provide deterministic behavior.
    """

    commands: list[list[str]] = []
    if (repo / "go.mod").exists():
        commands.append(["go", "list", "./..."])
    if (repo / "tsconfig.json").exists() or (repo / "package.json").exists():
        commands.append(["npx", "--yes", "tsc", "--noEmit", "--pretty", "false"])

    out: list[SymbolCandidate] = []
    for command in commands:
        try:
            proc = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = f"{proc.stdout}\n{proc.stderr}"
        if symbol not in text:
            continue
        for path, line, detail in _locations_from_compiler_output(repo, text):
            out.append(
                SymbolCandidate(
                    name=symbol,
                    kind="compiler-reference",
                    path=path,
                    line=line,
                    detail=detail,
                    source=command[0],
                    confidence="low",
                )
            )
    return out


def _locations_from_compiler_output(repo: Path, text: str) -> Iterable[tuple[str, int, str]]:
    for line in text.splitlines():
        match = re.search(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)[:(](\d+)", line)
        if not match:
            continue
        try:
            path = _safe_path(repo, match.group(1))
        except ValueError:
            continue
        if path.exists():
            yield path.relative_to(repo).as_posix(), int(match.group(2)), line[:240]


def _safe_path(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    root = repo.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Path escapes repository: {relative}")
    return candidate


def _line_from_location(value: object) -> int:
    if isinstance(value, dict):
        for key in ("line", "start_line", "lineno"):
            if key in value:
                try:
                    return max(1, int(value[key]))
                except (TypeError, ValueError):
                    pass
    match = re.search(r"(\d+)", str(value or ""))
    return max(1, int(match.group(1))) if match else 1


def _dedupe(candidates: list[SymbolCandidate]) -> list[SymbolCandidate]:
    best: dict[tuple[str, int, str, str], SymbolCandidate] = {}
    for candidate in candidates:
        key = (candidate.path, candidate.line, candidate.name, candidate.source)
        previous = best.get(key)
        if previous is None or _confidence_value(candidate.confidence) > _confidence_value(previous.confidence):
            best[key] = candidate
    return list(best.values())


def _rank(candidate: SymbolCandidate, symbol: str) -> tuple[int, int]:
    exact = 0 if candidate.name == symbol else 1
    source_rank = {
        "python-ast": 0,
        "graphify": 1,
        "static-pattern": 2,
    }.get(candidate.source, 3)
    return exact, source_rank


def _confidence_value(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)


def resolve_symbol_json(repo: Path, symbol: str, max_results: int = 20) -> str:
    return json.dumps(resolve_symbol(repo, symbol, max_results), indent=2)
