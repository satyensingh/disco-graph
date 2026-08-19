import json
from pathlib import Path
from types import SimpleNamespace

from app import graphify_service


def test_build_graph_invokes_code_only_extract(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git" / "info").mkdir(parents=True)

    monkeypatch.setattr(graphify_service.shutil, "which", lambda name: "/usr/local/bin/graphify")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        out = repo / "graphify-out"
        out.mkdir(exist_ok=True)
        (out / "graph.json").write_text(json.dumps({
            "nodes": [{"id": "A", "label": "A", "source_file": "A.java"}],
            "links": [],
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(graphify_service.subprocess, "run", fake_run)
    result = graphify_service.build_graph(repo, force=True)

    assert captured["cmd"][:4] == ["/usr/local/bin/graphify", "extract", ".", "--code-only"]
    assert "--force" in captured["cmd"]
    assert captured["cwd"] == repo
    assert result["summary"]["nodes"] == 1
    assert result["graph_html_path"] == "graphify-out/graph.html"
    html = (repo / "graphify-out" / "graph.html").read_text(encoding="utf-8")
    assert "disco-graph" in html
    assert "A.java" in html
    assert "sigma.js/2.4.0/sigma.min.js" in html
    assert "graphology/0.25.4/graphology.umd.min.js" in html
    assert "createSigmaRenderer" in html
    assert "createCanvasRenderer" in html
    assert "frontDecode" in html
    assert '"version":3' in html
    assert '"idPrefix"' in html
    assert '"pathRuns"' in html
    assert "__GRAPH_DATA__" not in html
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "graphify-out/" in exclude


def test_graph_status_reports_html_artifact(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "graphify-out").mkdir()
    (repo / "graphify-out" / "graph.json").write_text(json.dumps({
        "nodes": [{"id": "A", "label": "A", "source_file": "A.java"}],
        "links": [],
    }), encoding="utf-8")
    (repo / "graphify-out" / "graph.html").write_text("<html></html>", encoding="utf-8")

    result = graphify_service.graph_status(repo)

    assert result["graph_html_path"] == "graphify-out/graph.html"
    assert result["graph_html_exists"] is True


def test_write_graph_html_escapes_embedded_json(tmp_path: Path):
    graph_file = tmp_path / "graph.json"
    graph_file.write_text(json.dumps({
        "nodes": [{"id": "A", "label": "</script><b>A</b>", "source_file": "A.java"}],
        "links": [],
    }), encoding="utf-8")
    graph = graphify_service.CodeGraph.load(graph_file)
    html_file = tmp_path / "graph.html"

    graphify_service.write_graph_html(graph, html_file)
    html = html_file.read_text(encoding="utf-8")

    assert "</script><b>A</b>" not in html
    assert "\\u003c/script\\u003e" in html
