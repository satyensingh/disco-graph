import re
from pathlib import Path

from app.config import ensure_workspace_root, settings
from app.repo_manager import clone_repo


def test_workspace_root_falls_back_when_configured_path_is_unusable(monkeypatch, tmp_path: Path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("blocked", encoding="utf-8")
    unusable = blocker / "workspace"

    monkeypatch.setattr(settings, "workspace_root", unusable)

    root = ensure_workspace_root()

    assert root != unusable
    assert root.exists()
    assert root.is_dir()


def test_clone_repo_generates_uuid_repo_id(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "workspace_root", tmp_path)

    def fake_run(cmd, **kwargs):
        target = Path(cmd[-1])
        (target / ".git").mkdir(parents=True)
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("app.repo_manager.subprocess.run", fake_run)

    repo_id, path = clone_repo("https://github.com/example/project.git", "main", None)

    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        repo_id,
    )
    assert path == tmp_path / repo_id
    assert path.exists()
