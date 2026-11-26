from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from drx.agent.run import AgentRun, AgentRunState
from drx.evidence.execution import AgentEvidence
from drx.evidence.git import GitEvidence
from drx.evidence.test import TestEvidence
from drx.git.environment import git_environment

if TYPE_CHECKING:
    from drx.models.evidence import EvidenceRecord
    from drx.models.run import RunRecord
    from drx.models.task import TaskRecord


class EvidenceVerificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedRunEvidence:
    task_id: str
    run_id: str
    agent_id: str
    evidence_digest: str
    initial_commit: str
    final_commit: str
    changed_files: tuple[str, ...]
    diff_digest: str
    test_exit_code: int
    tests_run: int
    commit_created: bool
    _authority: object = field(repr=False, compare=False)


class EvidenceVerifier:
    def __init__(self) -> None:
        self._authority = object()

    def attest(
        self,
        *,
        task: TaskRecord,
        run: AgentRun,
        run_record: RunRecord,
        evidence_record: EvidenceRecord,
        evidence: AgentEvidence,
        git_evidence: GitEvidence,
        test_evidence: TestEvidence,
    ) -> VerifiedRunEvidence:
        self._validate_bindings(
            task=task,
            run=run,
            run_record=run_record,
            evidence_record=evidence_record,
            evidence=evidence,
            git_evidence=git_evidence,
            test_evidence=test_evidence,
        )
        if test_evidence.exit_code is None:
            raise EvidenceVerificationError("test evidence has no exit code")
        evidence_data = evidence.to_dict()
        try:
            persisted_data = json.loads(evidence_record.metadata_json)
        except json.JSONDecodeError as error:
            raise EvidenceVerificationError(
                "persisted execution evidence is not valid JSON"
            ) from error
        if persisted_data != evidence_data:
            raise EvidenceVerificationError(
                "persisted execution evidence does not match the collected evidence"
            )

        self._verify_workspace_git(evidence.workspace, run.id, git_evidence)
        evidence_digest = self._digest(evidence_data)
        return VerifiedRunEvidence(
            task_id=task.id,
            run_id=run.id,
            agent_id=run.agent_id,
            evidence_digest=evidence_digest,
            initial_commit=git_evidence.initial_head,
            final_commit=git_evidence.final_head,
            changed_files=git_evidence.changed_files,
            diff_digest=self._digest(git_evidence.diff),
            test_exit_code=test_evidence.exit_code,
            tests_run=test_evidence.tests_run,
            commit_created=git_evidence.commit_created,
            _authority=self._authority,
        )

    def verifies(
        self,
        evidence: VerifiedRunEvidence | None,
        *,
        task_id: str | None,
        run_id: str | None,
        agent_id: str | None,
    ) -> bool:
        return (
            isinstance(evidence, VerifiedRunEvidence)
            and evidence._authority is self._authority
            and evidence.task_id == task_id
            and evidence.run_id == run_id
            and evidence.agent_id == agent_id
            and bool(evidence.evidence_digest)
            and bool(evidence.diff_digest)
            and evidence.test_exit_code == 0
            and evidence.tests_run > 0
            and evidence.commit_created
            and bool(evidence.changed_files)
            and evidence.initial_commit != evidence.final_commit
        )

    @staticmethod
    def _validate_bindings(
        *,
        task: TaskRecord,
        run: AgentRun,
        run_record: RunRecord,
        evidence_record: EvidenceRecord,
        evidence: AgentEvidence,
        git_evidence: GitEvidence,
        test_evidence: TestEvidence,
    ) -> None:
        if task.status != "verifying":
            raise EvidenceVerificationError("Task is not awaiting Gate verification")
        if (
            run.state is not AgentRunState.EVIDENCE_COLLECTED
            or run_record.status != AgentRunState.EVIDENCE_COLLECTED.value
            or "test_pass" not in run.transitions
        ):
            raise EvidenceVerificationError(
                "AgentRun has no successful test and evidence transition"
            )
        if (
            task.id != run.task_id
            or run_record.id != run.id
            or run_record.task_id != task.id
            or evidence_record.task_id != task.id
            or evidence_record.run_id != run.id
            or evidence_record.producer != run.agent_id
            or evidence_record.type != "execution"
            or evidence_record.result != "pending"
            or evidence.task_id != task.id
            or evidence.run_id != run.id
            or evidence.agent_id != run.agent_id
        ):
            raise EvidenceVerificationError(
                "execution evidence is not bound to this Task, AgentRun, and Agent"
            )
        if (
            run_record.initial_commit != git_evidence.initial_head
            or run_record.final_commit != git_evidence.final_head
            or evidence.initial_commit != git_evidence.initial_head
            or evidence.final_commit != git_evidence.final_head
            or evidence.branch != git_evidence.branch
            or evidence.commit_created != git_evidence.commit_created
            or evidence.changed_files != git_evidence.changed_files
            or evidence.diff != git_evidence.diff
            or evidence.workspace != run_record.workspace
            or not run_record.workspace
        ):
            raise EvidenceVerificationError(
                "Git evidence does not match the AgentRun workspace and commits"
            )
        if (
            not git_evidence.commit_created
            or git_evidence.initial_head == git_evidence.final_head
            or not git_evidence.changed_files
            or not git_evidence.diff.strip()
        ):
            raise EvidenceVerificationError(
                "Git evidence has no committed workspace change"
            )
        if (
            not test_evidence.passed
            or test_evidence.timed_out
            or test_evidence.exit_code != 0
            or test_evidence.tests_run <= 0
            or evidence.test_exit_code != test_evidence.exit_code
            or evidence.test_command != test_evidence.command
            or evidence.test_stdout != test_evidence.stdout
            or evidence.test_stderr != test_evidence.stderr
            or evidence.test_output
            != f"{test_evidence.stdout}\n{test_evidence.stderr}"
            or evidence.test_duration_seconds != test_evidence.duration_seconds
        ):
            raise EvidenceVerificationError(
                "passing test evidence is missing or does not match the AgentRun"
            )

    @classmethod
    def _verify_workspace_git(
        cls, workspace: str, run_id: str, evidence: GitEvidence
    ) -> None:
        repository = Path(workspace).resolve()
        marker = repository.parent / ".devrelay-workspace"
        try:
            is_managed = (
                repository.is_dir()
                and marker.is_file()
                and marker.read_text(encoding="utf-8") == f"run-{run_id}"
            )
        except (OSError, UnicodeError) as error:
            raise EvidenceVerificationError(
                "could not verify the managed AgentRun workspace marker"
            ) from error
        if not is_managed:
            raise EvidenceVerificationError(
                "evidence workspace is not the managed AgentRun workspace"
            )
        if not re.fullmatch(r"[0-9a-f]{40,64}", evidence.initial_head) or not re.fullmatch(
            r"[0-9a-f]{40,64}", evidence.final_head
        ):
            raise EvidenceVerificationError("Git evidence contains invalid commit IDs")

        head = cls._git(repository, "rev-parse", "HEAD")
        if head != evidence.final_head:
            raise EvidenceVerificationError(
                "final Git commit does not match the current workspace HEAD"
            )
        branch = cls._git(repository, "branch", "--show-current")
        if branch != evidence.branch:
            raise EvidenceVerificationError(
                "current Git branch does not match Git evidence"
            )
        if cls._git(repository, "status", "--short"):
            raise EvidenceVerificationError(
                "managed workspace contains uncommitted changes"
            )
        cls._git(
            repository,
            "merge-base",
            "--is-ancestor",
            evidence.initial_head,
            evidence.final_head,
        )
        changed_files = tuple(
            sorted(
                set(
                    cls._git(
                        repository,
                        "diff",
                        "--name-only",
                        evidence.initial_head,
                        evidence.final_head,
                    ).splitlines()
                )
            )
        )
        if changed_files != evidence.changed_files:
            raise EvidenceVerificationError(
                "committed file list does not match Git evidence"
            )
        diff = cls._git(
            repository,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            evidence.initial_head,
            evidence.final_head,
        )
        if diff != evidence.diff:
            raise EvidenceVerificationError(
                "committed Git diff does not match Git evidence"
            )
        if (
            cls._git(
                repository,
                "rev-list",
                "--count",
                f"{evidence.initial_head}..{evidence.final_head}",
            )
            != "1"
        ):
            raise EvidenceVerificationError(
                "final Git evidence must contain exactly one managed commit"
            )

    @staticmethod
    def _git(repository: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=git_environment(),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise EvidenceVerificationError(
                "could not independently verify workspace Git evidence"
            ) from error
        if result.returncode:
            raise EvidenceVerificationError(
                "workspace Git evidence failed independent verification"
            )
        return result.stdout.strip()

    @staticmethod
    def _digest(value: object) -> str:
        serialized = (
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            if not isinstance(value, str)
            else value
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
