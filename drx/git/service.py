import subprocess
from pathlib import Path

from drx.git.environment import git_environment


class GitService:
    _read_only_calls = {
        ("status",),
        ("status", "--short"),
        ("rev-parse", "HEAD"),
        ("branch", "--show-current"),
        ("diff",),
        ("log", "-1", "--oneline"),
    }

    def __init__(
        self, repository: str | Path, *, allow_local_commit: bool = True
    ) -> None:
        self.repository = Path(repository)
        self.allow_local_commit = allow_local_commit

    def run(self, *args: str) -> str:
        if args not in self._read_only_calls:
            raise PermissionError("Git command is not allowed by the local policy")
        return self._execute(*args)

    def _execute(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repository,
            check=True,
            capture_output=True,
            text=True,
            env=git_environment(),
        )
        return result.stdout.strip()

    def status(self) -> str:
        return self.run("status", "--short")

    def commit(self, message: str) -> str:
        if not self.allow_local_commit:
            raise PermissionError("local Git commits are disabled by policy")
        self._execute("add", "-A")
        return self._execute("commit", "-m", message)
