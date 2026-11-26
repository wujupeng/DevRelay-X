import subprocess
from pathlib import Path

import pytest

from drx.evidence.git import GitEvidenceCollector
from drx.git.service import GitService


def _init_repository(path: Path, content: str = "value = 1\n") -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "DevRelay test"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "devrelay-test@example.invalid"],
        cwd=path,
        check=True,
    )
    (path / "source.py").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=path, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_git_diff_is_applied_and_collected(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    initial = _init_repository(repository)
    patch = (
        "diff --git a/source.py b/source.py\n"
        "--- a/source.py\n"
        "+++ b/source.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 2\n"
    )
    collector = GitEvidenceCollector(repository)
    collector.apply_patch(patch)
    evidence = collector.collect(initial)
    assert evidence.initial_head == evidence.final_head
    assert evidence.changed_files == ("source.py",)
    assert "+value = 2" in evidence.diff


def test_git_diff_accepts_a_complete_markdown_diff_fence(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    _init_repository(
        repository,
        "def add(left: int, right: int) -> int:\n"
        "    return None\n"
        "\n"
        "def subtract(left: int, right: int) -> int:\n"
        "    return None\n",
    )
    collector = GitEvidenceCollector(repository)
    patch = (
        "```diff\n"
        "diff --git a/source.py b/source.py\n"
        "index 1234567..89abcdef 100644\n"
        "--- a/source.py\n"
        "+++ b/source.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def add(left: int, right: int) -> int:\n"
        "-    return None\n"
        "+    return left + right\n"
        "\n"
        " def subtract(left: int, right: int) -> int:\n"
        "-    return None\n"
        "+    return left - right\n"
        "```\n"
    )

    collector.apply_patch(patch)

    assert (repository / "source.py").read_text(encoding="utf-8") == (
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
        "\n"
        "def subtract(left: int, right: int) -> int:\n"
        "    return left - right\n"
    )


def test_git_diff_rejects_secret_files_and_path_traversal(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    _init_repository(repository)
    collector = GitEvidenceCollector(repository)
    with pytest.raises(PermissionError):
        collector.apply_patch("--- a/../outside.txt\n+++ b/../outside.txt\n")
    with pytest.raises(PermissionError):
        collector.apply_patch(
            "```diff\n"
            "--- a/../outside.txt\n"
            "+++ b/../outside.txt\n"
            "@@ -0,0 +1 @@\n"
            "+outside\n"
            "```\n"
        )
    with pytest.raises(PermissionError):
        collector.apply_patch("--- /dev/null\n+++ b/.env\n")


def test_git_diff_rejects_binary_patch_and_git_service_push(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    _init_repository(repository)
    collector = GitEvidenceCollector(repository)
    with pytest.raises(PermissionError, match="binary"):
        collector.apply_patch("diff --git a/image.png b/image.png\nGIT binary patch\n")
    with pytest.raises(PermissionError):
        GitService(repository).run("push")
    with pytest.raises(PermissionError, match="disabled"):
        GitService(repository, allow_local_commit=False).commit("not allowed")


def test_git_service_creates_local_agent_commit(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    initial_head = _init_repository(repository)
    (repository / "source.py").write_text("value = 2\n", encoding="utf-8")

    commit_hash = GitService(repository).commit("DevRelay-X local test")
    author_name = subprocess.run(
        ["git", "show", "-s", "--format=%an", commit_hash],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert commit_hash != initial_head
    assert author_name == "DevRelay-X Local Agent"


def test_git_evidence_retains_changes_after_a_local_commit(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    initial_head = _init_repository(repository)
    collector = GitEvidenceCollector(repository)
    collector.apply_patch(
        "diff --git a/source.py b/source.py\n"
        "--- a/source.py\n"
        "+++ b/source.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 2\n"
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture change"],
        cwd=repository,
        check=True,
        capture_output=True,
    )

    evidence = collector.collect(initial_head)

    assert evidence.commit_created
    assert evidence.initial_head != evidence.final_head
    assert evidence.changed_files == ("source.py",)
    assert "+value = 2" in evidence.diff
