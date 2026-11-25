from dataclasses import dataclass

from drx.evidence.execution import redact_secrets


@dataclass(frozen=True, slots=True)
class TaskPromptInput:
    project_id: str
    project_name: str
    task_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    workspace_path: str
    required_tests: tuple[str, ...]
    workspace_context: str = ""


class TaskPromptBuilder:
    def build(self, task: TaskPromptInput) -> str:
        criteria = (
            "\n".join(
                f"- {redact_secrets(item)}" for item in task.acceptance_criteria
            )
            or "- None supplied"
        )
        tests = (
            "\n".join(f"- {item}" for item in task.required_tests)
            or "- Run detected project tests"
        )
        return f"""You are a local coding agent working only on the assigned task.

Project: {task.project_name} (ID: {task.project_id})
Task ID: {task.task_id}
Objective:
{redact_secrets(task.objective)}

Acceptance criteria:
{criteria}

Required tests:
{tests}

Repository context:
{redact_secrets(task.workspace_context) or "[no supported text files found]"}

Allowed workspace: {task.workspace_path}

Return only a unified Git diff that applies from the workspace repository root.
Do not use markdown fences or include explanations outside the diff.
An empty diff is not successful completion.

Prohibited operations:
- Do not declare or imply Gate PASS; only DevRelay-X evaluates acceptance.
- Do not merge, push, deploy, or modify Git remotes.
- Do not access or modify any repository outside the allowed workspace.
- Do not modify DevRelay-X governance rules or bypass required tests.
- Do not fabricate test output, Git state, or evidence.
- Do not request, read, or include credentials, tokens, or secret values.

DevRelay-X will validate and apply your diff, execute the configured tests, and
collect Git and test evidence independently."""
