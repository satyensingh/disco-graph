from pathlib import Path
import tempfile

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-5.6"
    openai_reasoning_effort: str = "medium"

    workspace_root: Path = Path("/workspace")
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "disco-graph-artifacts"
    minio_prefix: str = "disco-graph"
    minio_public_endpoint: str = ""
    minio_region: str = "us-east-1"
    minio_secure: bool = False
    minio_required: bool = False
    minio_timeout_seconds: int = 5
    minio_url_expiry_seconds: int = 3600
    max_agent_steps: int = 18
    command_timeout_seconds: int = 180
    graph_build_timeout_seconds: int = 900
    allow_arbitrary_commands: bool = False
    max_tool_output_chars: int = 20000

    auto_index_on_run: bool = True
    graph_default_bfs_depth: int = 1
    graph_max_bfs_depth: int = 5
    graph_seed_limit: int = 8
    graph_max_nodes: int = 100
    graph_sufficiency_threshold: float = 0.68

    isolated_worktrees: bool = False
    keep_isolated_worktrees: bool = True
    openai_input_cost_per_million: float = 0.0
    openai_output_cost_per_million: float = 0.0


settings = Settings()


def ensure_workspace_root() -> Path:
    settings.workspace_root = _usable_workspace_root(settings.workspace_root)
    return settings.workspace_root


def _usable_workspace_root(path: Path) -> Path:
    try:
        _assert_writable_directory(path)
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "disco-graph-workspace"
        _assert_writable_directory(fallback)
        return fallback


def _assert_writable_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".disco-graph-write-test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


ensure_workspace_root()
