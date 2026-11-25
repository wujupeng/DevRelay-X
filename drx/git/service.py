import subprocess
from pathlib import Path


class GitService:
    def __init__(self, repository: str | Path) -> None:
        self.repository = Path(repository)

    def run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repository,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def status(self) -> str:
        return self.run("status", "--short")

    def commit(self, message: str) -> str:
        self.run("add", "-A")
        return self.run("commit", "-m", message)
