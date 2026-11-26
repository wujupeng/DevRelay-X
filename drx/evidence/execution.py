import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b(?:ghp_|github_pat_|glpat-|sk-)[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\b(?:xox[baprs]-)[A-Za-z0-9-]{10,}\b"),
    re.compile(
        r"(?i)((?:api[_-]?key|[a-z0-9_-]*token|password|secret|credential)\s*[:=]\s*['\"]?)[^\s,'\";]+"
    ),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", redacted)
    return redacted


@dataclass(frozen=True, slots=True)
class AgentEvidence:
    task_id: str
    run_id: str
    agent_id: str
    runtime_id: str
    workspace: str
    started_at: datetime
    ended_at: datetime
    initial_commit: str
    final_commit: str
    branch: str
    commit_created: bool
    changed_files: tuple[str, ...]
    test_command: tuple[str, ...]
    test_exit_code: int | None
    test_stdout: str
    test_stderr: str
    test_output: str
    test_duration_seconds: float | None
    git_status: str
    diff_summary: str
    diff: str
    execution_result: str
    failure_reason: str
    gate_decision: str = "blocked"
    gate_results: tuple[dict[str, str], ...] = ()
    state_transitions: tuple[str, ...] = ()
    verification_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        data["ended_at"] = self.ended_at.isoformat()
        data["changed_files"] = list(self.changed_files)
        data["test_command"] = list(self.test_command)
        data["gate_results"] = list(self.gate_results)
        data["state_transitions"] = list(self.state_transitions)
        data["diff"] = redact_secrets(self.diff)
        data["test_stdout"] = redact_secrets(self.test_stdout)
        data["test_stderr"] = redact_secrets(self.test_stderr)
        data["test_output"] = redact_secrets(self.test_output)
        data["failure_reason"] = redact_secrets(self.failure_reason)
        return data
