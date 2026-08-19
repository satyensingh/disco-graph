import json
from pathlib import Path

from app.symbol_resolver import resolve_symbol


def test_resolve_symbol_combines_python_ast_and_graph(tmp_path: Path):
    repo = tmp_path
    (repo / "src").mkdir()
    (repo / "src" / "service.py").write_text(
        "class OrderService:\n"
        "    def reserve(self):\n"
        "        return True\n",
        encoding="utf-8",
    )
    out = repo / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps({
        "nodes": [
            {
                "id": "pkg.OrderService",
                "label": "OrderService",
                "type": "class",
                "source_file": "src/service.py",
                "source_location": "L1",
            }
        ],
        "links": [],
    }), encoding="utf-8")

    result = resolve_symbol(repo, "OrderService")
    candidates = result["candidates"]

    assert candidates
    assert candidates[0]["path"] == "src/service.py"
    assert {candidate["source"] for candidate in candidates} >= {"python-ast", "graphify"}


def test_resolve_symbol_finds_typescript_exports(tmp_path: Path):
    repo = tmp_path
    (repo / "src").mkdir()
    (repo / "src" / "cart.ts").write_text(
        "export function addToCart(id: string) {\n"
        "  return id;\n"
        "}\n",
        encoding="utf-8",
    )

    result = resolve_symbol(repo, "addToCart")

    assert result["candidates"][0]["kind"] == "function"
    assert result["candidates"][0]["path"] == "src/cart.ts"
