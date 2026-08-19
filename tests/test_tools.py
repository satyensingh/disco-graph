from pathlib import Path
import tempfile

import pytest

from app.tools import safe_path


def test_safe_path_accepts_repo_child():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        assert safe_path(repo, "src/main.py") == (repo / "src/main.py").resolve()


def test_safe_path_blocks_parent_escape():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        with pytest.raises(ValueError):
            safe_path(repo, "../secret.txt")
