import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from drx.evidence.execution import redact_secrets
from drx.git.environment import git_environment


@dataclass(frozen=True, slots=True)
class GitEvidence:
    initial_head: str
    final_head: str
    branch: str
    status: str
    changed_files: tuple[str, ...]
    diff_summary: str
    diff: str
    commit_created: bool


class GitEvidenceCollector:
    def __init__(self, repository: str | Path) -> None:
        self.repository = Path(repository).resolve()

    def collect(self, initial_head: str) -> GitEvidence:
        self._run("add", "--intent-to-add", "--", ".")
        final_head = self._run("rev-parse", "HEAD")
        changed = self._run("diff", "--name-only", initial_head).splitlines()
        untracked = self._run("ls-files", "--others", "--exclude-standard").splitlines()
        changed_files = tuple(sorted(set(changed + untracked)))
        return GitEvidence(
            initial_head=initial_head,
            final_head=final_head,
            branch=self._run("branch", "--show-current"),
            status=self._run("status", "--short"),
            changed_files=changed_files,
            diff_summary=self._run(
                "diff", "--no-ext-diff", "--no-textconv", "--stat", initial_head
            ),
            diff=self._run(
                "diff", "--no-ext-diff", "--no-textconv", "--binary", initial_head
            ),
            commit_created=initial_head != final_head,
        )

    def apply_patch(self, patch: str) -> None:
        patch = patch.replace("\r\n", "\n").replace("\r", "\n")
        lines = patch.strip().splitlines()
        if (
            len(lines) >= 3
            and lines[0].strip().lower() == "```diff"
            and lines[-1].strip() == "```"
        ):
            patch = "\n".join(lines[1:-1]).strip() + "\n"
        if not patch.startswith(("diff --git ", "--- ")):
            raise ValueError("runtime output is not a unified Git diff")
        for line in patch.splitlines():
            if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
                raise PermissionError("file renames and copies are not permitted in model diffs")
            if line == "GIT binary patch":
                raise PermissionError("binary patches are not permitted")
            if line.startswith(("new file mode 120000", "old mode 120000")):
                raise PermissionError("symbolic links are not permitted in model diffs")
            if line.startswith("diff --git "):
                paths = shlex.split(line[len("diff --git ") :])
                if len(paths) != 2:
                    raise PermissionError("diff header contains an ambiguous file path")
                for raw_path in paths:
                    self._validate_path(raw_path)
            if not line.startswith(("--- ", "+++ ")):
                continue
            raw_path = line[4:].split("\t", 1)[0].strip()
            if raw_path == "/dev/null":
                continue
            self._validate_path(raw_path)
        self._run_input(
            ["apply", "--recount", "--ignore-whitespace", "--check", "--"], patch
        )
        self._run_input(["apply", "--recount", "--ignore-whitespace", "--"], patch)

    def _validate_path(self, raw_path: str) -> None:
        if raw_path.startswith('"'):
            parsed_paths = shlex.split(raw_path)
            if len(parsed_paths) != 1:
                raise PermissionError("diff contains an ambiguous quoted path")
            raw_path = parsed_paths[0]
        if raw_path.startswith(("a/", "b/")):
            raw_path = raw_path[2:]
        path = Path(raw_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or any(part.lower() == ".git" for part in path.parts)
            or ":" in raw_path
        ):
            raise PermissionError("diff contains a path outside the allowed workspace")
        candidate = self.repository
        for part in path.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise PermissionError("diff traverses a symbolic link")
        lowered = path.name.lower()
        if (
            lowered.startswith(".env")
            or lowered.endswith((".pem", ".key", ".p12", ".pfx"))
            or lowered in {"id_rsa", "id_ed25519"}
        ):
            raise PermissionError("diff attempts to modify a protected secret file")

    def _run(self, *args: str) -> str:
        return self._run_input(list(args))

    def _run_input(self, args: list[str], input_text: str | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repository,
            input=input_text.encode("utf-8") if input_text is not None else None,
            check=False,
            capture_output=True,
            timeout=60,
            env=git_environment(),
        )
        if result.returncode:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            details = redact_secrets(stderr)[:500]
            raise RuntimeError(f"Git evidence operation failed: {details}")
        return result.stdout.decode("utf-8", errors="replace").strip()
