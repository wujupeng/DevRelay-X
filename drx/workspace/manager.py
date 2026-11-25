import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from drx.evidence.execution import redact_secrets
from drx.git.environment import git_environment


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    root: Path
    repository: Path
    initial_head: str
    branch: str


class WorkspaceManager:
    _workspace_id = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    _marker = ".devrelay-workspace"

    def __init__(
        self,
        root: str | Path,
        *,
        control_repository: str | Path,
        allow_control_repository: bool = False,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.control_repository = Path(control_repository).resolve()
        self.allow_control_repository = allow_control_repository
        if (
            not allow_control_repository
            and (
                self.root == self.control_repository
                or self.control_repository in self.root.parents
            )
        ):
            raise WorkspaceError("workspace root cannot be inside the control repository")
        self._managed_workspaces: dict[Path, str] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def _validate_source(self, repository: str | Path) -> str:
        source = str(repository)
        parsed = urlsplit(source)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise WorkspaceError("repository credentials in URLs are not permitted")
        local_source = Path(source).expanduser()
        if local_source.exists():
            resolved_source = local_source.resolve()
            if (
                not self.allow_control_repository
                and (
                    resolved_source == self.control_repository
                    or self.control_repository in resolved_source.parents
                )
            ):
                raise WorkspaceError("cloning the DevRelay-X control repository is denied")
            return str(resolved_source)
        elif parsed.scheme and parsed.scheme not in {"https", "ssh", "git"}:
            raise WorkspaceError("unsupported repository URL scheme")
        return source

    def create(
        self,
        workspace_id: str,
        repository: str | Path,
        *,
        branch: str | None = None,
    ) -> Workspace:
        if not self._workspace_id.fullmatch(workspace_id):
            raise WorkspaceError("workspace ID contains unsafe path characters")
        source = self._validate_source(repository)
        workspace_root = (self.root / workspace_id).resolve()
        if workspace_root.parent != self.root or workspace_root.exists():
            raise WorkspaceError("workspace destination is invalid or already exists")

        workspace_root.mkdir()
        marker = workspace_root / self._marker
        marker.write_text(workspace_id, encoding="utf-8")
        self._managed_workspaces[workspace_root] = workspace_id
        repository_path = workspace_root / "repository"
        try:
            args = ["git", "clone", "--"]
            if branch:
                args = ["git", "clone", "--branch", branch, "--"]
            result = subprocess.run(
                [*args, source, str(repository_path)],
                cwd=self.root,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
                env=git_environment(),
            )
            if result.returncode:
                raise WorkspaceError("Git clone failed; check the configured repository")
            head = self._git(repository_path, "rev-parse", "HEAD")
            current_branch = self._git(repository_path, "branch", "--show-current")
            return Workspace(workspace_id, workspace_root, repository_path, head, current_branch)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self.cleanup(workspace_root, workspace_id)
            raise

    def prepare_workspace(self, workspace: Workspace) -> Workspace:
        if not workspace.repository.is_dir():
            raise WorkspaceError("workspace repository is unavailable")
        return workspace

    def collect_context(self, workspace: Workspace, *, max_chars: int = 50000) -> str:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=workspace.repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=git_environment(),
        )
        if result.returncode:
            raise WorkspaceError("cannot enumerate workspace source files")
        allowed_suffixes = {
            ".c",
            ".cfg",
            ".css",
            ".go",
            ".h",
            ".html",
            ".java",
            ".js",
            ".json",
            ".md",
            ".py",
            ".rs",
            ".sh",
            ".sql",
            ".toml",
            ".ts",
            ".tsx",
            ".txt",
            ".yaml",
            ".yml",
        }
        protected_names = {"id_rsa", "id_ed25519"}
        sections: list[str] = []
        used_chars = 0
        root = workspace.repository.resolve()
        for raw_path in sorted(result.stdout.split("\0")):
            if not raw_path:
                continue
            relative = Path(raw_path)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or any(
                    part.lower() in {".git", "node_modules", ".venv", "vendor"}
                    for part in relative.parts
                )
            ):
                continue
            if (
                relative.name.lower().startswith(".env")
                or relative.name.lower() in protected_names
                or any(
                    token in relative.name.lower()
                    for token in ("secret", "credential", "token")
                )
                or relative.suffix.lower() in {".pem", ".key", ".p12"}
                or relative.suffix.lower() not in allowed_suffixes
            ):
                continue
            file_path = workspace.repository / relative
            if file_path.is_symlink() or not file_path.is_file():
                continue
            resolved_path = file_path.resolve()
            try:
                resolved_path.relative_to(root)
            except ValueError:
                continue
            if file_path.stat().st_size > max_chars:
                continue
            content = redact_secrets(
                file_path.read_text(encoding="utf-8", errors="replace")
            )
            section = f"### {relative.as_posix()}\n```text\n{content}\n```"
            if used_chars + len(section) > max_chars:
                break
            sections.append(section)
            used_chars += len(section)
        return "\n\n".join(sections)

    def cleanup(self, path: str | Path, workspace_id: str) -> None:
        target = Path(path)
        if target.is_symlink():
            raise WorkspaceError("refusing to clean up a symlink")
        resolved = target.resolve()
        if resolved.parent != self.root or resolved == self.root:
            raise WorkspaceError("cleanup target is outside the managed workspace root")
        if self._managed_workspaces.get(resolved) != workspace_id:
            raise WorkspaceError("workspace was not created by this manager instance")
        marker = resolved / self._marker
        if not marker.is_file() or marker.read_text(encoding="utf-8") != workspace_id:
            raise WorkspaceError("cleanup target is not a workspace managed by this node")
        shutil.rmtree(resolved, onexc=self._make_writable_and_retry)
        del self._managed_workspaces[resolved]

    @staticmethod
    def _git(repository: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=git_environment(),
        )
        if result.returncode:
            raise WorkspaceError("Git operation failed in the managed workspace")
        return result.stdout.strip()

    @staticmethod
    def _make_writable_and_retry(
        function: Callable[[str], object],
        path: str,
        error: BaseException,
    ) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(path, stat.S_IWRITE)
        function(path)
