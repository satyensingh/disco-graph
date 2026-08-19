# disco-graph V3 — Graph-Aware Autonomous Coding Agent

disco-graph V3 is a containerized coding agent that combines role-specific agent execution with a persistent, AST-derived code graph. It can clone a repository, build a Graphify graph, resolve symbols with graph/static/compiler-aware probes, retrieve a bounded subgraph using explicit BFS depth, patch inside isolated Git worktrees, run tests/builds, and report token/cost measurements for benchmarking.

V3 is designed around one rule: **the graph narrows the search space; source code and tests remain authoritative.**

## What changed from V2

V3 keeps the V2 graph retrieval contract and adds:

- `resolve_symbol`, which combines Graphify metadata, Python AST parsing, TS/JS/Java-style static definitions, and cheap compiler probes when project tooling exists.
- Detached Git worktree execution via `isolated_worktree`, so benchmark runs do not mutate the source checkout.
- Separate Planner, Coder and Reviewer role wrappers; every tool event is tagged with the responsible role.
- Token, cost, tool-call and elapsed-time metrics on `/agent/run` responses.
- `/benchmarks/run` to compare `graph_adaptive` against `repo_scan` in isolated worktrees.

## Architecture

```text
                        Coding Task
                             |
                             v
                    +----------------+
                    | disco-graph     |
                    | agent           |
                    |  LLM decision  |
                    +-------+--------+
                            |
          +-----------------+------------------+
          |                                    |
          v                                    v
 +-------------------+                +-------------------+
 | Graph Intelligence|                | Direct Repo Tools |
 +-------------------+                +-------------------+
 | graph_status      |                | repo_map          |
 | graph_build       |                | ripgrep search    |
 | graph_query       |                | read_file         |
 | graph_neighbors   |                | apply_patch       |
 | graph_path        |                | run_command       |
 | resolve_symbol    |                | git_diff          |
 +---------+---------+                +-------------------+
           |
           v
 +--------------------------+
 | Graphify code-only index |
 | tree-sitter AST parsing  |
 +-------------+------------+
               |
               v
               graphify-out/graph.json
      graphify-out/graph.html
               |
               v
 +--------------------------+
 | disco-graph BFS Retriever |
 | depth / relation / conf. |
 +-------------+------------+
               |
               v
      diagnostics + evidence
               |
               +-------> Agent decides:
                         enough -> read source
                         weak radius -> depth + 1
                         bad seeds -> reformulate
                         exact text -> ripgrep
```

## Adaptive BFS behavior

The model is not given a fixed depth for every question.

For a task like:

> Fix the bug where an order reaches payment but inventory is not reserved.

disco-graph can do:

```text
graph_query("order inventory payment flow", depth=1)
        |
        +-- score 0.42, evidence thin, recommended depth=2
        v
graph_query("order inventory payment flow", depth=2)
        |
        +-- finds OrderService -> InventoryService -> ReservationRepository
        v
read exact source files
        v
patch -> targeted test -> verify diff
```

If the first query has bad seed wording, the agent can instead reformulate at the same depth. If the task is really about a literal configuration key or error string, it can switch to ripgrep rather than increasing graph radius.

This distinction is important: **BFS depth is one retrieval control, not a universal answer to poor search results.**

## Graphify integration

disco-graph builds code graphs with:

```bash
graphify extract . --code-only
```

The code-only pass is local AST extraction and does not require a Graphify semantic-model API key. The generated graph is kept inside each repository at:

```text
graphify-out/graph.json
```

When running with Docker Compose, the graph artifacts are also uploaded to MinIO under the `disco-graph` root prefix:

```text
disco-graph/<repo_id>/graphify-out/graph.json
disco-graph/<repo_id>/graphify-out/graph.html
disco-graph/<repo_id>/manifest.json
```

The local files are a working cache used by graph retrieval. The index and status responses include the MinIO object metadata and a temporary `graph_html_url` when MinIO is enabled. The MinIO console is available at `http://localhost:9001` with the credentials configured in `.env`.

disco-graph parses the graph directly so it can control BFS depth itself. The Graphify CLI's natural-language query command has its own retrieval behavior/token budget, but disco-graph does not depend on that behavior for adaptive depth.

Graphify artifacts are added to `.git/info/exclude` locally, so disco-graph does not need to modify the repository's tracked `.gitignore` just to hide its index.

## 1. Configure

```bash
cp .env.example .env
```

Set at minimum:

```env
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-5.6
```

Useful V3 settings:

```env
OPENAI_REASONING_EFFORT=medium
AUTO_INDEX_ON_RUN=true
GRAPH_DEFAULT_BFS_DEPTH=1
GRAPH_MAX_BFS_DEPTH=5
GRAPH_SEED_LIMIT=8
GRAPH_MAX_NODES=100
GRAPH_SUFFICIENCY_THRESHOLD=0.68
MINIO_ENDPOINT=minio:9000
MINIO_PUBLIC_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=change-this-minio-password
MINIO_BUCKET=disco-graph-artifacts
MINIO_PREFIX=disco-graph
MINIO_REGION=us-east-1
MINIO_REQUIRED=false
MINIO_TIMEOUT_SECONDS=5
MAX_AGENT_STEPS=18
ISOLATED_WORKTREES=false
KEEP_ISOLATED_WORKTREES=true
OPENAI_INPUT_COST_PER_MILLION=0
OPENAI_OUTPUT_COST_PER_MILLION=0
```

For lower cost, you can change `OPENAI_MODEL` without modifying source code. Cost estimates are configurable because provider pricing changes; token counts come from model response usage when available.

## 2. Start

```bash
docker compose up --build
```

Then open Swagger:

```text
http://localhost:8080/docs
```

Health:

```bash
curl http://localhost:8080/health
```

## 3. Clone a repository

```bash
curl -X POST http://localhost:8080/repos/clone \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_url": "https://github.com/OWNER/REPO.git",
    "branch": "main"
  }'
```

Private HTTPS repository:

```bash
curl -X POST http://localhost:8080/repos/clone \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_url": "https://github.com/OWNER/REPO.git",
    "branch": "main",
    "pat": "YOUR_PAT"
  }'
```

The PAT is provided to Git using `GIT_ASKPASS`; disco-graph does not rewrite the remote URL with the token.
Each clone receives a fresh UUID v4 `repo_id`. Save the `repo_id` from the clone response and use it in subsequent index, graph, agent, and benchmark requests.

Example response:

```json
{
  "repo_id": "2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
  "path": "/workspace/2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
  "branch": "main"
}
```

## 4. Build the AST graph explicitly

You can let `/agent/run` do this automatically, or pre-index once:

```bash
curl -X POST http://localhost:8080/repos/index \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_id": "2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
    "force": false
  }'
```

Check graph status:

```bash
curl http://localhost:8080/repos/2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a/graph/status
```

A graph is reported as `stale` when repository files are newer than `graph.json`. The index response and status response include `graph_html_path`; open that file to inspect the generated Sigma.js/Graphology interactive graph visualization. The HTML embeds the graph data but loads the visualization libraries from CDN, so the browser needs network access unless those scripts are later vendored.

## 5. Experiment with BFS manually

Depth 1:

```bash
curl -X POST http://localhost:8080/repos/graph/query \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_id": "2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
    "query": "how does catalog reach pricing",
    "depth": 1,
    "max_nodes": 60
  }'
```

Then compare depth 2:

```bash
curl -X POST http://localhost:8080/repos/graph/query \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_id": "2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
    "query": "how does catalog reach pricing",
    "depth": 2,
    "max_nodes": 60
  }'
```

By default the endpoint runs in autonomous mode. `depth` is the starting BFS depth; the endpoint can widen depth, increase `max_nodes`, relax filters, or reformulate the graph seed query before answering. Set `"auto": false` to force a single retrieval with exactly the supplied parameters.

The response returns a plain-English `answer`, preserves the final retrieval fields, and includes an `attempts` trace showing how the answer was gathered:

```json
{
  "answer": "The graph connects catalog-facing code to pricing through ...",
  "answer_source": "llm",
  "retrieval_mode": "auto",
  "original_query": "how does catalog reach pricing",
  "query": "how does catalog reach pricing",
  "depth": 2,
  "nodes": [],
  "edges": [],
  "diagnostics": {},
  "attempts": [
    {
      "step": 1,
      "query": "how does catalog reach pricing",
      "depth": 1,
      "diagnostics": {"sufficient": false, "recommended_next_depth": 2}
    },
    {
      "step": 2,
      "query": "how does catalog reach pricing",
      "depth": 2,
      "diagnostics": {"sufficient": true}
    }
  ],
  "stop_reason": "llm_answer"
}
```

If the LLM is not configured or fails, `answer_source` is `fallback` and the response includes `llm_error`.

Example diagnostic block:

```json
{
  "sufficiency_score": 0.61,
  "query_token_coverage": 0.75,
  "source_file_count": 2,
  "node_count": 9,
  "edge_count": 7,
  "sufficient": false,
  "recommended_next_depth": 2,
  "reason": "..."
}
```

The score is a retrieval heuristic, not a correctness score. The agent must still inspect source.

You can focus on explicit relationships:

```json
{
  "repo_id": "2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
  "query": "OrderService repository calls",
  "depth": 2,
  "relations": ["calls", "imports"],
  "confidences": ["EXTRACTED"]
}
```

## 6. Run the autonomous coding agent

```bash
curl -X POST http://localhost:8080/agent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_id": "2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
    "task": "Fix the failing OrderService test when the customer has no saved address. Find the actual dependency flow first, make the smallest correct change, and run the relevant tests.",
    "max_steps": 18,
    "auto_index": true,
    "isolated_worktree": true,
    "retrieval_strategy": "graph_adaptive"
  }'
```

The response includes every visible tool event, role tags, metrics, worktree metadata, and the final active-worktree diff. Set `"retrieval_strategy": "repo_scan"` to disable graph tools for a baseline run.

## 7. Benchmark graph retrieval against repo scanning

```bash
curl -X POST http://localhost:8080/benchmarks/run \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_id": "2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
    "task": "Fix the failing OrderService test when the customer has no saved address.",
    "max_steps": 18,
    "strategies": ["graph_adaptive", "repo_scan"],
    "validation_command": "pytest -q"
  }'
```

Each strategy runs in its own detached worktree. The response includes per-strategy status, diff, role-tagged events, token/cost metrics, optional validation output, and a comparison block with graph-minus-scan token and cost deltas.

## Agent actions in V3

| Action | Purpose |
|---|---|
| `graph_status` | Check whether the AST graph exists/is stale |
| `graph_build` | Build/rebuild Graphify code graph |
| `graph_query` | Retrieve a subgraph with explicit BFS depth |
| `graph_neighbors` | Expand around a known symbol |
| `graph_path` | Find a bounded shortest path between symbols |
| `resolve_symbol` | Resolve definitions using graph/static/compiler-aware probes |
| `repo_map` | Lightweight repository file map |
| `search` | ripgrep code/text search |
| `read_file` | Read exact line ranges |
| `apply_patch` | Apply a unified diff after validation |
| `run_command` | Run an allow-listed developer command |
| `git_diff` | Inspect final changes |
| `finish` | End the agent loop |

## Example Java task

For a Spring Boot repository:

```json
{
  "repo_id": "2f2b4c30-2a0f-4d3a-bf0e-ef5a8c1c0f4a",
  "task": "McAirJetTest.currentShiftReport is failing. Trace the production-efficiency flow from the test through service and repository code, identify the smallest root-cause fix, apply it, and run only the relevant integration test first.",
  "max_steps": 24
}
```

A good V3 trajectory should look more like:

```text
graph_query("McAirJetTest currentShiftReport production efficiency", depth=1)
 -> graph_query(..., depth=2) only if needed
 -> read McAirJetTest
 -> read connected service/repository symbols
 -> search exact metric/SQL string if needed
 -> apply patch
 -> ./gradlew ... targeted test
 -> inspect failure/success
 -> git_diff
 -> finish
```

and less like scanning thousands of files indiscriminately.

## Safety boundaries

The repository and its build scripts are untrusted input.

- Repository file reads are constrained to the checkout.
- Parent-directory escapes are blocked.
- No `shell=True` command execution.
- Developer commands are allow-listed by default.
- Destructive Git operations such as push/reset/clean/checkout are blocked.
- PATs are not stored in the Git remote URL.
- Agent runs have a configurable step limit.
- BFS has a hard maximum depth and maximum node count.
- No automatic commit or push exists.
- Isolated worktrees are detached from the source checkout when enabled.

`ALLOW_ARBITRARY_COMMANDS=true` removes the command allowlist. Use that only inside a disposable sandbox.

### Production warning

Although disco-graph itself constrains commands, `mvn test`, `./gradlew test`, `npm test`, Makefiles, and similar build tools execute repository-controlled code. A production multi-user coding agent should run each task in a disposable container/VM with CPU, memory, filesystem, network, and credential restrictions.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
uvicorn app.main:app --reload --port 8080
```

Run tests:

```bash
pytest -q
```

The included tests cover:

- repository path escape protection,
- Graphify JSON loading,
- BFS radius expansion,
- confidence filtering,
- shortest paths,
- graph-aware patch workflow,
- an agent explicitly widening BFS from depth 1 to depth 2 based on retrieval diagnostics,
- compiler/static symbol resolution,
- isolated worktree execution,
- role-tagged agent runs,
- benchmark comparison metrics.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Health/version |
| POST | `/repos/clone` | Clone repository + branch |
| POST | `/repos/index` | Build Graphify AST graph |
| GET | `/repos/{repo_id}/graph/status` | Inspect index state |
| POST | `/repos/graph/query` | Run manual bounded BFS retrieval |
| POST | `/agent/run` | Run autonomous coding task |
| POST | `/benchmarks/run` | Compare graph-adaptive and repo-scan strategies |

## What V3 deliberately does not do yet

V3 is graph-aware, role-aware and benchmarkable, but it is still a local developer prototype. Useful next additions are:

1. Long-lived LSP server protocol sessions for deeper definition/reference accuracy.
2. Ephemeral runner containers separate from the API container.
3. Resumable run state in SQLite/PostgreSQL.
4. Automated correctness scoring against a curated set of real repository bugs.
5. GitHub PR creation only after an explicit approval gate.
