from drx.execution.prompt import TaskPromptBuilder, TaskPromptInput


def test_prompt_sets_workspace_and_rejects_agent_gate_authority() -> None:
    prompt = TaskPromptBuilder().build(
        TaskPromptInput(
            project_id="project",
            project_name="Calculator",
            task_id="task-1",
            objective="Implement arithmetic; API_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456",
            acceptance_criteria=("add returns sum",),
            workspace_path="C:/workspaces/task",
            required_tests=("pytest",),
        )
    )
    assert "Task ID: task-1" in prompt
    assert "Allowed workspace: C:/workspaces/task" in prompt
    assert "Do not declare or imply Gate PASS" in prompt
    assert "Do not merge, push, deploy" in prompt
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in prompt
