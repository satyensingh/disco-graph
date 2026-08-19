from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .code_graph import CodeGraph
from .config import settings
from .storage import get_graph_artifact_storage


GRAPH_RELATIVE_PATH = Path("graphify-out/graph.json")
GRAPH_HTML_RELATIVE_PATH = Path("graphify-out/graph.html")
GRAPH_TEMPLATE_PATH = Path(__file__).with_name("graph_template.html")


def graph_path(repo: Path) -> Path:
    return repo / GRAPH_RELATIVE_PATH


def graph_html_path(repo: Path) -> Path:
    return repo / GRAPH_HTML_RELATIVE_PATH


def _storage_repo_id(repo: Path) -> str:
    if repo.parent.name == ".disco-graph-worktrees":
        git_pointer = repo / ".git"
        if git_pointer.is_file():
            content = git_pointer.read_text(encoding="utf-8", errors="replace").strip()
            if content.startswith("gitdir:"):
                gitdir = Path(content.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (repo / gitdir).resolve()
                parents = list(gitdir.parents)
                if len(parents) > 2:
                    return parents[2].name
        match = re.match(r"^([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})-", repo.name)
        if match:
            return match.group(1)
    return repo.name


def graph_available(repo: Path) -> bool:
    path = graph_path(repo)
    if path.exists():
        return True
    return get_graph_artifact_storage().restore(
        _storage_repo_id(repo),
        path,
        graph_html_path(repo),
    )


def _ensure_local_exclude(repo: Path) -> None:
    exclude = repo / ".git" / "info" / "exclude"
    if not exclude.parent.exists():
        return
    existing = exclude.read_text(encoding="utf-8", errors="replace") if exclude.exists() else ""
    additions = ["graphify-out/", "graph.json"]
    missing = [entry for entry in additions if entry not in existing.splitlines()]
    if missing:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("\n# disco-graph local code-graph artifacts\n")
            for entry in missing:
                fh.write(entry + "\n")


def build_graph(repo: Path, *, force: bool = False) -> dict[str, Any]:
    binary = shutil.which("graphify")
    if not binary:
        raise RuntimeError("Graphify CLI is not installed. Install the 'graphifyy' Python package.")
    _ensure_local_exclude(repo)
    cmd = [binary, "extract", ".", "--code-only"]
    if force:
        cmd.append("--force")
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=settings.graph_build_timeout_seconds,
        env={**os.environ, "CI": "true"},
    )
    elapsed = round(time.monotonic() - started, 3)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "graphify extract failed").strip())
    path = graph_path(repo)
    if not path.exists():
        raise RuntimeError("Graphify completed but graphify-out/graph.json was not created")
    graph = CodeGraph.load(path)
    html_path = graph_html_path(repo)
    write_graph_html(graph, html_path)
    storage = get_graph_artifact_storage().sync(
        _storage_repo_id(repo),
        path,
        html_path,
        graph.summary(),
    )
    return {
        "status": "built",
        "graph_path": str(GRAPH_RELATIVE_PATH),
        "graph_html_path": str(GRAPH_HTML_RELATIVE_PATH),
        "elapsed_seconds": elapsed,
        "summary": graph.summary(),
        "storage": storage,
        "graph_html_url": storage.get("graph_html_url"),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def graph_status(repo: Path) -> dict[str, Any]:
    path = graph_path(repo)
    graph_available(repo)
    storage = get_graph_artifact_storage().describe(_storage_repo_id(repo))
    if not path.exists():
        return {
            "exists": False,
            "graph_path": str(GRAPH_RELATIVE_PATH),
            "graph_html_path": str(GRAPH_HTML_RELATIVE_PATH),
            "graph_html_exists": False,
            "storage": storage,
            "graph_html_url": storage.get("graph_html_url"),
        }
    graph = CodeGraph.load(path)
    stat = path.stat()
    html = graph_html_path(repo)
    latest_source_mtime = 0.0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in {".git", "graphify-out", "node_modules", "build", "dist", "target", ".gradle", ".venv", "venv"}]
        for name in files:
            try:
                latest_source_mtime = max(latest_source_mtime, (Path(root) / name).stat().st_mtime)
            except OSError:
                pass
    return {
        "exists": True,
        "graph_path": str(GRAPH_RELATIVE_PATH),
        "graph_html_path": str(GRAPH_HTML_RELATIVE_PATH),
        "graph_html_exists": html.exists(),
        "storage": storage,
        "graph_html_url": storage.get("graph_html_url"),
        "modified_epoch": stat.st_mtime,
        "stale": latest_source_mtime > stat.st_mtime + 0.001,
        "summary": graph.summary(),
    }


def load_graph(repo: Path) -> CodeGraph:
    path = graph_path(repo)
    if not graph_available(repo):
        raise FileNotFoundError("No Graphify graph found. Build it with graph_build first.")
    return CodeGraph.load(path)


def write_graph_html(graph: CodeGraph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_packed_graph_payload(graph), ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    path.write_text(_graph_html_document(payload), encoding="utf-8")


def _packed_graph_payload(graph: CodeGraph) -> dict[str, Any]:
    node_items = sorted(
        graph.nodes.items(),
        key=lambda item: (
            str(item[1].get("source_file") or ""),
            str(item[1].get("label") or item[1].get("name") or item[0]),
            item[0],
        ),
    )
    node_index = {node_id: index for index, (node_id, _node) in enumerate(node_items)}
    ids = [node_id for node_id, _node in node_items]
    labels = [
        str(node.get("label") or node.get("name") or node_id)
        for node_id, node in node_items
    ]
    paths = [
        str(node.get("source_file") or "")
        for _node_id, node in node_items
    ]
    kinds = [
        str(node.get("type") or node.get("kind") or "node")
        for _node_id, node in node_items
    ]

    path_vocab, path_runs = _vocabulary_runs(paths)
    kind_vocab, kind_runs = _vocabulary_runs(kinds)
    relation_vocab = sorted({edge.relation or "related" for edge in graph.edges}) or ["related"]
    confidence_vocab = sorted({edge.confidence or "UNKNOWN" for edge in graph.edges}) or ["UNKNOWN"]
    relation_index = {value: index for index, value in enumerate(relation_vocab)}
    confidence_index = {value: index for index, value in enumerate(confidence_vocab)}

    edge_source: list[int] = []
    edge_target: list[int] = []
    edge_relation: list[int] = []
    edge_confidence: list[int] = []
    for edge in graph.edges:
        if edge.source not in node_index or edge.target not in node_index:
            continue
        edge_source.append(node_index[edge.source])
        edge_target.append(node_index[edge.target])
        edge_relation.append(relation_index[edge.relation or "related"])
        edge_confidence.append(confidence_index[edge.confidence or "UNKNOWN"])

    id_prefix, id_suffix = _front_encode(ids)
    label_prefix, label_suffix = _front_encode(labels)
    return {
        "version": 3,
        "summary": graph.summary(),
        "nodes": {
            "count": len(node_items),
            "idPrefix": id_prefix,
            "idSuffix": id_suffix,
            "labelPrefix": label_prefix,
            "labelSuffix": label_suffix,
            "paths": path_vocab,
            "pathRuns": path_runs,
            "kinds": kind_vocab,
            "kindRuns": kind_runs,
        },
        "edges": {
            "relations": relation_vocab,
            "confidences": confidence_vocab,
            "source": edge_source,
            "target": edge_target,
            "relation": edge_relation,
            "confidence": edge_confidence,
        },
    }


def _front_encode(values: list[str]) -> tuple[list[int], list[str]]:
    prefixes: list[int] = []
    suffixes: list[str] = []
    previous = ""
    for value in values:
        prefix = 0
        limit = min(len(previous), len(value))
        while prefix < limit and previous[prefix] == value[prefix]:
            prefix += 1
        prefixes.append(prefix)
        suffixes.append(value[prefix:])
        previous = value
    return prefixes, suffixes


def _vocabulary_runs(values: list[str]) -> tuple[list[str], list[int]]:
    vocabulary: list[str] = []
    indexes: dict[str, int] = {}
    encoded: list[int] = []
    for value in values:
        if value not in indexes:
            indexes[value] = len(vocabulary)
            vocabulary.append(value)
        encoded.append(indexes[value])
    return vocabulary, _run_length_encode(encoded)


def _run_length_encode(values: list[int]) -> list[int]:
    if not values:
        return []
    runs: list[int] = []
    current = values[0]
    count = 1
    for value in values[1:]:
        if value == current:
            count += 1
            continue
        runs.extend([current, count])
        current = value
        count = 1
    runs.extend([current, count])
    return runs


def _graph_html_document(payload: str) -> str:
    if GRAPH_TEMPLATE_PATH.exists():
        return GRAPH_TEMPLATE_PATH.read_text(encoding="utf-8").replace("__GRAPH_DATA__", payload)
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>disco-graph</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/graphology/0.25.4/graphology.umd.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/sigma.js/2.4.0/sigma.min.js"></script>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5f6b7a;
      --border: #d8dee8;
      --accent: #1f7a8c;
      --edge: #8a95a5;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      overflow: hidden;
    }
    header {
      height: 58px;
      display: flex;
      align-items: center;
      gap: 18px;
      padding: 0 18px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 16px;
      font-weight: 650;
      white-space: nowrap;
    }
    .stat {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    input {
      width: min(420px, 36vw);
      height: 34px;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      margin-left: auto;
    }
    select,
    button {
      height: 34px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
      padding: 0 10px;
    }
    button { cursor: pointer; }
    main {
      display: grid;
      grid-template-columns: 1fr 320px;
      height: calc(100vh - 58px);
    }
    #graph-container {
      position: relative;
      width: 100%;
      height: 100%;
      background: #fbfcfe;
    }
    .overlay {
      position: absolute;
      inset: 16px auto auto 16px;
      z-index: 2;
      max-width: 520px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.94);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      box-shadow: 0 8px 24px rgba(23, 32, 42, 0.08);
    }
    .overlay[hidden] { display: none; }
    aside {
      border-left: 1px solid var(--border);
      background: var(--panel);
      padding: 16px;
      overflow: auto;
      font-size: 13px;
    }
    .label { color: var(--muted); margin-top: 14px; }
    .value { margin-top: 5px; line-height: 1.4; overflow-wrap: anywhere; }
    .hint { color: var(--muted); line-height: 1.45; }
    @media (max-width: 760px) {
      header { flex-wrap: wrap; height: auto; min-height: 58px; padding: 10px 12px; }
      input { width: 100%; margin-left: 0; }
      select,
      button { flex: 1 1 auto; }
      main { grid-template-columns: 1fr; }
      aside { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>disco-graph</h1>
    <div class="stat" id="counts"></div>
    <input id="search" type="search" placeholder="Filter symbols or files">
    <select id="relation"></select>
    <button id="reset" type="button">Reset</button>
  </header>
  <main>
    <div id="graph-container">
      <div class="overlay" id="loading">Loading interactive graph...</div>
    </div>
    <aside>
      <div class="hint">Powered by Sigma.js and Graphology. Drag to pan, wheel or pinch to zoom, hover to highlight, and click a node to inspect it.</div>
      <div class="label">Selected</div>
      <div class="value" id="selected">None</div>
      <div class="label">Connections</div>
      <div class="value" id="connections">None</div>
    </aside>
  </main>
  <script id="graph-data" type="application/json">__GRAPH_DATA__</script>
  <script>
    const data = JSON.parse(document.getElementById("graph-data").textContent);
    const container = document.getElementById("graph-container");
    const search = document.getElementById("search");
    const relation = document.getElementById("relation");
    const reset = document.getElementById("reset");
    const selectedEl = document.getElementById("selected");
    const connectionsEl = document.getElementById("connections");
    const countsEl = document.getElementById("counts");
    const loadingEl = document.getElementById("loading");
    const colors = ["#1f7a8c", "#586f7c", "#8f5f3c", "#34623f", "#7b6d2e", "#725752"];
    try {
    if (!window.graphology || !window.Sigma) throw new Error("Sigma.js or Graphology failed to load");
    const nodeDegrees = new Map();
    for (const edge of data.edges) {
      nodeDegrees.set(edge.source, (nodeDegrees.get(edge.source) || 0) + 1);
      nodeDegrees.set(edge.target, (nodeDegrees.get(edge.target) || 0) + 1);
    }
    const groups = new Map();
    for (const node of data.nodes) {
      const group = groupName(node.source_file);
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(node);
    }
    const groupNames = [...groups.keys()].sort();
    const groupCenters = new Map();
    const groupRadius = Math.max(240, Math.sqrt(data.nodes.length) * 34);
    groupNames.forEach((group, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(1, groupNames.length);
      groupCenters.set(group, {
        x: Math.cos(angle) * groupRadius,
        y: Math.sin(angle) * groupRadius,
        color: colors[index % colors.length]
      });
    });
    const rawNodes = data.nodes.map(node => ({
      ...node,
      degree: nodeDegrees.get(node.id) || 0,
      group: groupName(node.source_file),
      visible: true
    }));
    const nodes = rawNodes;
    const placedById = new Map(nodes.map(node => [node.id, node]));
    for (const [group, groupNodes] of groups) {
      const center = groupCenters.get(group);
      const sorted = groupNodes
        .slice()
        .sort((a, b) => (nodeDegrees.get(b.id) || 0) - (nodeDegrees.get(a.id) || 0));
      sorted.forEach((node, index) => {
        const placed = placedById.get(node.id);
        const ring = Math.floor(Math.sqrt(index));
        const angle = index * 2.399963;
        const radius = 18 + ring * 16;
        placed.x = center.x + Math.cos(angle) * radius;
        placed.y = center.y + Math.sin(angle) * radius;
        placed.color = center.color;
      });
    }
    const nodeById = new Map(nodes.map(node => [node.id, node]));
    const edges = data.edges.filter(edge => nodeById.has(edge.source) && nodeById.has(edge.target));
    const edgesByNode = new Map(nodes.map(node => [node.id, []]));
    const adjacencyAll = new Map(nodes.map(node => [node.id, new Set()]));
    const adjacencyByRelation = new Map();
    for (const edge of edges) {
      const rel = edge.relation || "related";
      edgesByNode.get(edge.source).push(edge);
      edgesByNode.get(edge.target).push(edge);
      adjacencyAll.get(edge.source).add(edge.target);
      adjacencyAll.get(edge.target).add(edge.source);
      if (!adjacencyByRelation.has(rel)) {
        adjacencyByRelation.set(rel, new Map(nodes.map(node => [node.id, new Set()])));
      }
      const relationAdjacency = adjacencyByRelation.get(rel);
      relationAdjacency.get(edge.source).add(edge.target);
      relationAdjacency.get(edge.target).add(edge.source);
    }
    const relations = [...new Set(edges.map(edge => edge.relation || "related"))].sort();
    relation.innerHTML = `<option value="">All relations</option>${relations.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")}`;

    const graph = window.graphology.MultiGraph ? new window.graphology.MultiGraph() : new window.graphology.Graph({ multi: true, allowSelfLoops: true });
    for (const node of nodes) {
      graph.addNode(node.id, {
        label: String(node.label || node.id),
        x: node.x,
        y: node.y,
        size: Math.max(2.5, Math.min(9, 3 + node.degree * 0.18)),
        color: node.color,
        kind: node.type || "node",
        source_file: node.source_file || "",
        group: node.group,
        degree: node.degree
      });
    }
    edges.forEach((edge, index) => {
      graph.addEdgeWithKey(`e${index}`, edge.source, edge.target, {
        label: edge.relation || "related",
        relation: edge.relation || "related",
        confidence: edge.confidence || "UNKNOWN",
        size: edge.confidence === "EXTRACTED" ? 0.8 : 0.45,
        color: edge.confidence === "EXTRACTED" ? "#aab3c1" : "#c9ced8"
      });
    });

    let selectedNode = null;
    let hoveredNode = null;
    let matchingNodes = null;
    let activeRelation = "";
    const renderer = new window.Sigma(graph, container, {
      allowInvalidContainer: true,
      defaultNodeColor: "#1f7a8c",
      defaultEdgeColor: "#b9c0cc",
      labelRenderedSizeThreshold: 8,
      labelDensity: 0.08,
      labelGridCellSize: 90,
      renderEdgeLabels: false,
      enableEdgeClickEvents: true,
      enableEdgeHoverEvents: true
    });
    container.dataset.renderer = "sigma";
    loadingEl.hidden = true;
    countsEl.textContent = `${graph.order} nodes, ${graph.size} edges`;

    function groupName(sourceFile) {
      if (!sourceFile) return "unknown";
      const parts = String(sourceFile).split("/");
      return parts.length > 1 ? parts[0] : "(root)";
    }

    function isNodeVisible(node) {
      return !matchingNodes || matchingNodes.has(node);
    }

    function edgeMatchesRelation(edge) {
      return !activeRelation || graph.getEdgeAttribute(edge, "relation") === activeRelation;
    }

    function dataEdgeMatchesRelation(edge) {
      return !activeRelation || (edge.relation || "related") === activeRelation;
    }

    function isAdjacentByActiveRelation(node, focus) {
      if (node === focus) return true;
      const adjacency = activeRelation ? adjacencyByRelation.get(activeRelation) : adjacencyAll;
      return Boolean(adjacency && adjacency.get(focus) && adjacency.get(focus).has(node));
    }

    function renderSelectedDetails() {
      if (!selectedNode) {
        selectedEl.textContent = "None";
        connectionsEl.textContent = "None";
        return;
      }
      const attrs = graph.getNodeAttributes(selectedNode);
      selectedEl.innerHTML = `<strong>${escapeHtml(attrs.label || selectedNode)}</strong><br>${escapeHtml(attrs.kind || "node")}<br>${escapeHtml(attrs.source_file || "")}<br>${escapeHtml(attrs.group || "")}<br>degree: ${escapeHtml(attrs.degree || 0)}`;
      const connected = (edgesByNode.get(selectedNode) || [])
        .filter(edge => dataEdgeMatchesRelation(edge) && (edge.source === selectedNode || edge.target === selectedNode))
        .slice(0, 20)
        .map(edge => `${escapeHtml(edge.source)} ${escapeHtml(edge.relation)} ${escapeHtml(edge.target)}`);
      connectionsEl.innerHTML = connected.length ? connected.join("<br>") : "None";
    }

    renderer.setSetting("nodeReducer", (node, attrs) => {
      const next = { ...attrs };
      if (!isNodeVisible(node)) {
        next.hidden = true;
        return next;
      }
      if (selectedNode || hoveredNode) {
        const focus = selectedNode || hoveredNode;
        const adjacent = isAdjacentByActiveRelation(node, focus);
        if (!adjacent) {
          next.color = "#d6dbe4";
          next.label = "";
        } else {
          next.size = attrs.size + 2;
        }
      }
      if (node === selectedNode) {
        next.color = "#d1495b";
        next.highlighted = true;
      }
      return next;
    });

    renderer.setSetting("edgeReducer", (edge, attrs) => {
      const source = graph.source(edge);
      const target = graph.target(edge);
      const next = { ...attrs };
      if (!edgeMatchesRelation(edge) || !isNodeVisible(source) || !isNodeVisible(target)) {
        next.hidden = true;
        return next;
      }
      if (selectedNode || hoveredNode) {
        const focus = selectedNode || hoveredNode;
        if (source !== focus && target !== focus) {
          next.hidden = true;
        } else {
          next.color = "#d1495b";
          next.size = Math.max(attrs.size || 1, 1.6);
        }
      }
      return next;
    });

    function setSelected(node) {
      selectedNode = node;
      renderSelectedDetails();
      renderer.refresh();
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[char]));
    }

    function applyFilter() {
      const term = search.value.trim().toLowerCase();
      activeRelation = relation.value;
      let visibleNodeCount = 0;
      let visibleEdgeCount = 0;
      matchingNodes = term ? new Set() : null;
      for (const node of nodes) {
        const matches = !term || `${node.id} ${node.label} ${node.type} ${node.source_file} ${node.group}`.toLowerCase().includes(term);
        if (matches) {
          visibleNodeCount += 1;
          if (matchingNodes) matchingNodes.add(node.id);
        }
      }
      for (const edge of edges) {
        if ((!matchingNodes || (matchingNodes.has(edge.source) && matchingNodes.has(edge.target))) && (!activeRelation || edge.relation === activeRelation)) {
          visibleEdgeCount += 1;
        }
      }
      countsEl.textContent = `${visibleNodeCount} of ${nodes.length} nodes, ${visibleEdgeCount} of ${edges.length} edges`;
      if (selectedNode && matchingNodes && !matchingNodes.has(selectedNode)) setSelected(null);
      else renderSelectedDetails();
      renderer.refresh();
    }

    renderer.on("clickNode", event => setSelected(event.node));
    renderer.on("clickStage", () => setSelected(null));
    renderer.on("enterNode", event => {
      hoveredNode = event.node;
      renderer.refresh();
    });
    renderer.on("leaveNode", () => {
      hoveredNode = null;
      renderer.refresh();
    });
    search.addEventListener("input", applyFilter);
    relation.addEventListener("change", applyFilter);
    reset.addEventListener("click", () => {
      search.value = "";
      relation.value = "";
      setSelected(null);
      renderer.getCamera().animatedReset();
      applyFilter();
    });
    applyFilter();
    } catch (error) {
      console.error(error);
      initCanvasFallback(data, error);
    }

    function initCanvasFallback(data, error) {
      container.innerHTML = "";
      container.dataset.renderer = "canvas";
      const notice = document.createElement("div");
      notice.className = "overlay";
      notice.textContent = `Using built-in canvas fallback: ${error.message || error}`;
      container.appendChild(notice);
      const canvas = document.createElement("canvas");
      container.appendChild(canvas);
      const ctx = canvas.getContext("2d");
      const degree = new Map();
      for (const edge of data.edges) {
        degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
        degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
      }
      const groups = new Map();
      for (const node of data.nodes) {
        const group = fallbackGroup(node.source_file);
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push(node);
      }
      const groupNames = [...groups.keys()].sort();
      const centers = new Map();
      const baseRadius = Math.max(240, Math.sqrt(data.nodes.length) * 34);
      groupNames.forEach((group, index) => {
        const angle = (Math.PI * 2 * index) / Math.max(1, groupNames.length);
        centers.set(group, { x: Math.cos(angle) * baseRadius, y: Math.sin(angle) * baseRadius, color: colors[index % colors.length] });
      });
      const nodes = data.nodes.map(node => ({
        ...node,
        degree: degree.get(node.id) || 0,
        group: fallbackGroup(node.source_file),
        visible: true
      }));
      const byId = new Map(nodes.map(node => [node.id, node]));
      for (const [group, groupNodes] of groups) {
        const center = centers.get(group);
        groupNodes
          .slice()
          .sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0))
          .forEach((node, index) => {
            const placed = byId.get(node.id);
            const ring = Math.floor(Math.sqrt(index));
            const angle = index * 2.399963;
            const radius = 18 + ring * 16;
            placed.x = center.x + Math.cos(angle) * radius;
            placed.y = center.y + Math.sin(angle) * radius;
            placed.color = center.color;
          });
      }
      const edges = data.edges
        .filter(edge => byId.has(edge.source) && byId.has(edge.target))
        .map(edge => ({ ...edge, sourceNode: byId.get(edge.source), targetNode: byId.get(edge.target) }));
      const canvasEdgesByNode = new Map(nodes.map(node => [node.id, []]));
      for (const edge of edges) {
        canvasEdgesByNode.get(edge.source).push(edge);
        canvasEdgesByNode.get(edge.target).push(edge);
      }
      const relationValues = [...new Set(edges.map(edge => edge.relation || "related"))].sort();
      relation.innerHTML = `<option value="">All relations</option>${relationValues.map(value => `<option value="${fallbackEsc(value)}">${fallbackEsc(value)}</option>`).join("")}`;
      let scale = 1;
      let offsetX = 0;
      let offsetY = 0;
      let selected = null;
      let canvasActiveRelation = "";
      let dragging = false;
      let lastX = 0;
      let lastY = 0;
      let visibleNodes = nodes;
      let visibleEdges = edges;

      function fallbackGroup(sourceFile) {
        if (!sourceFile) return "unknown";
        const parts = String(sourceFile).split("/");
        return parts.length > 1 ? parts[0] : "(root)";
      }

      function fallbackEsc(value) {
        return String(value).replace(/[&<>"']/g, char => ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;"
        }[char]));
      }

      function resizeCanvas() {
        const rect = container.getBoundingClientRect();
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.max(1, Math.floor(rect.width * ratio));
        canvas.height = Math.max(1, Math.floor(rect.height * ratio));
        canvas.style.width = `${rect.width}px`;
        canvas.style.height = `${rect.height}px`;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        drawCanvas();
      }

      function world(pointX, pointY) {
        const rect = canvas.getBoundingClientRect();
        return {
          x: (pointX - rect.left - rect.width / 2 - offsetX) / scale,
          y: (pointY - rect.top - rect.height / 2 - offsetY) / scale
        };
      }

      function drawCanvas() {
        const rect = canvas.getBoundingClientRect();
        ctx.clearRect(0, 0, rect.width, rect.height);
        ctx.save();
        ctx.translate(rect.width / 2 + offsetX, rect.height / 2 + offsetY);
        ctx.scale(scale, scale);
        ctx.lineWidth = 1 / scale;
        ctx.strokeStyle = "#aab3c1";
        ctx.globalAlpha = visibleEdges.length > 10000 ? 0.18 : 0.34;
        for (const edge of visibleEdges) {
          ctx.beginPath();
          ctx.moveTo(edge.sourceNode.x, edge.sourceNode.y);
          ctx.lineTo(edge.targetNode.x, edge.targetNode.y);
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
        const drawLabels = scale > 0.8 && visibleNodes.length <= 2500;
        for (const node of visibleNodes) {
          const active = selected && selected.id === node.id;
          ctx.beginPath();
          ctx.fillStyle = active ? "#d1495b" : node.color;
          ctx.arc(node.x, node.y, active ? 7 : Math.max(2.4, Math.min(6, 2.5 + node.degree * 0.16)), 0, Math.PI * 2);
          ctx.fill();
          if (drawLabels || active) {
            ctx.font = `${Math.max(10, 12 / scale)}px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif`;
            ctx.fillStyle = "#17202a";
            ctx.fillText(String(node.label || node.id), node.x + 9, node.y + 4);
          }
        }
        ctx.restore();
      }

      function pick(clientX, clientY) {
        const p = world(clientX, clientY);
        let best = null;
        let bestDist = 12 / scale;
        for (const node of visibleNodes) {
          const d = Math.hypot(node.x - p.x, node.y - p.y);
          if (d < bestDist) {
            best = node;
            bestDist = d;
          }
        }
        return best;
      }

      function setCanvasSelected(node) {
        selected = node;
        renderCanvasSelectedDetails();
        drawCanvas();
      }

      function renderCanvasSelectedDetails() {
        if (!selected) {
          selectedEl.textContent = "None";
          connectionsEl.textContent = "None";
          return;
        }
        selectedEl.innerHTML = `<strong>${fallbackEsc(selected.label || selected.id)}</strong><br>${fallbackEsc(selected.type || "node")}<br>${fallbackEsc(selected.source_file || "")}<br>${fallbackEsc(selected.group || "")}<br>degree: ${fallbackEsc(selected.degree || 0)}`;
        const connected = (canvasEdgesByNode.get(selected.id) || [])
          .filter(edge => (!canvasActiveRelation || (edge.relation || "related") === canvasActiveRelation) && (edge.source === selected.id || edge.target === selected.id))
          .slice(0, 20)
          .map(edge => `${fallbackEsc(edge.source)} ${fallbackEsc(edge.relation)} ${fallbackEsc(edge.target)}`);
        connectionsEl.innerHTML = connected.length ? connected.join("<br>") : "None";
      }

      function applyCanvasFilter() {
        const term = search.value.trim().toLowerCase();
        canvasActiveRelation = relation.value;
        visibleNodes = nodes.filter(node => !term || `${node.id} ${node.label} ${node.type} ${node.source_file} ${node.group}`.toLowerCase().includes(term));
        const visibleIds = new Set(visibleNodes.map(node => node.id));
        visibleEdges = edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target) && (!canvasActiveRelation || edge.relation === canvasActiveRelation));
        countsEl.textContent = `${visibleNodes.length} of ${nodes.length} nodes, ${visibleEdges.length} of ${edges.length} edges`;
        if (selected && !visibleIds.has(selected.id)) setCanvasSelected(null);
        else renderCanvasSelectedDetails();
        drawCanvas();
      }

      canvas.addEventListener("mousedown", event => {
        dragging = true;
        lastX = event.clientX;
        lastY = event.clientY;
      });
      window.addEventListener("mouseup", () => dragging = false);
      window.addEventListener("mousemove", event => {
        if (!dragging) return;
        offsetX += event.clientX - lastX;
        offsetY += event.clientY - lastY;
        lastX = event.clientX;
        lastY = event.clientY;
        drawCanvas();
      });
      canvas.addEventListener("click", event => setCanvasSelected(pick(event.clientX, event.clientY)));
      canvas.addEventListener("wheel", event => {
        event.preventDefault();
        const before = world(event.clientX, event.clientY);
        scale = Math.max(0.08, Math.min(5, scale * (event.deltaY < 0 ? 1.12 : 0.89)));
        const after = world(event.clientX, event.clientY);
        offsetX += (after.x - before.x) * scale;
        offsetY += (after.y - before.y) * scale;
        drawCanvas();
      }, { passive: false });
      search.addEventListener("input", applyCanvasFilter);
      relation.addEventListener("change", applyCanvasFilter);
      reset.addEventListener("click", () => {
        search.value = "";
        relation.value = "";
        scale = 1;
        offsetX = 0;
        offsetY = 0;
        setCanvasSelected(null);
        applyCanvasFilter();
      });
      window.addEventListener("resize", resizeCanvas);
      resizeCanvas();
      applyCanvasFilter();
    }
  </script>
</body>
</html>
""".replace("__GRAPH_DATA__", payload)


def graph_query(
    repo: Path,
    query: str,
    depth: int,
    *,
    max_nodes: int | None = None,
    relations: list[str] | None = None,
    confidences: list[str] | None = None,
) -> str:
    if not query.strip():
        raise ValueError("query is required")
    graph = load_graph(repo)
    result = graph.retrieve(
        query,
        depth,
        max_nodes=max_nodes or settings.graph_max_nodes,
        seed_limit=settings.graph_seed_limit,
        relations=relations,
        confidences=confidences,
        sufficiency_threshold=settings.graph_sufficiency_threshold,
        max_depth=settings.graph_max_bfs_depth,
    )
    return json.dumps(result, indent=2, ensure_ascii=False)


def graph_neighbors(repo: Path, node: str, depth: int = 1, max_nodes: int = 60) -> str:
    graph = load_graph(repo)
    return json.dumps(graph.neighbors(node, depth, max_nodes), indent=2, ensure_ascii=False)


def graph_shortest_path(repo: Path, source: str, target: str, max_hops: int = 8) -> str:
    graph = load_graph(repo)
    return json.dumps(graph.shortest_path(source, target, max_hops), indent=2, ensure_ascii=False)
