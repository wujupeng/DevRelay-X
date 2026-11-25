import subprocess
from pathlib import Path

import pytest

from drx.workspace.manager import WorkspaceError, WorkspaceManager


def _init_repository(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "DevRelay test"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "devrelay-test@example.invalid"],
        cwd=path,
        check=True,
    )
    (path / "README.txt").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=path, check=True, capture_output=True)


def test_workspace_is_cloned_and_cleanup_is_scoped(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _init_repository(source)
    manager = WorkspaceManager(
        tmp_path / "workspaces", control_repository=tmp_path / "control"
    )
    workspace = manager.create("run-1", source)
    assert workspace.initial_head
    assert workspace.repository.joinpath("README.txt").is_file()
    manager.cleanup(workspace.root, workspace.id)
    assert not workspace.root.exists()


def test_workspace_rejects_traversal_and_control_repository(tmp_path: Path) -> None:
    control = tmp_path / "control"
    _init_repository(control)
    manager = WorkspaceManager(tmp_path / "workspaces", control_repository=control)
    with pytest.raises(WorkspaceError, match="unsafe path"):
        manager.create("../escape", control)
    with pytest.raises(WorkspaceError, match="control repository"):
        manager.create("run-2", control)
    with pytest.raises(WorkspaceError, match="workspace root"):
        WorkspaceManager(control / "workspaces", control_repository=control)


def test_workspace_context_excludes_env_files_and_redacts_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _init_repository(source)
    (source / "config.py").write_text(
        'API_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8"
    )
    (source / ".env").write_text("API_TOKEN=private\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add local config"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    manager = WorkspaceManager(
        tmp_path / "workspaces", control_repository=tmp_path / "control"
    )
    workspace = manager.create("run-context", source)

    context = manager.collect_context(workspace)

    assert "config.py" in context
    assert "[REDACTED]" in context
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in context
    assert ".env" not in context
