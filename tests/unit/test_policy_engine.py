import pytest

from drx.agent.capabilities import AgentCapabilities, AgentCapability
from drx.policy.engine import PolicyEngine


def test_policy_allows_explicit_default_action_and_denies_unknown_actions() -> None:
    policy = PolicyEngine()
    workspace_capability = AgentCapabilities(
        (AgentCapability("workspace-isolation", "Uses a managed workspace"),)
    )

    assert not policy.evaluate("workspace.create").allowed
    assert policy.evaluate(
        "workspace.create",
        capabilities=workspace_capability,
        passed_gates={"G0_ENVIRONMENT"},
    ).allowed
    assert not policy.evaluate(
        "workspace.create", capabilities=workspace_capability
    ).allowed
    assert not policy.evaluate(
        "git.commit", capabilities=workspace_capability, passed_gates={"G4_TEST"}
    ).allowed
    assert not policy.evaluate(
        "shell.execute", capabilities=workspace_capability
    ).allowed
    assert not policy.evaluate("git.push", capabilities=workspace_capability).allowed
    assert not policy.evaluate("", capabilities=workspace_capability).allowed


def test_policy_requires_registered_capability_and_prerequisite_gates() -> None:
    policy = PolicyEngine()
    generation_capability = AgentCapabilities(
        (AgentCapability("runtime-generation", "Requests model changes"),)
    )

    assert not policy.evaluate(
        "runtime.generate",
        capabilities=generation_capability,
        passed_gates={"G0_ENVIRONMENT", "G1_WORKSPACE", "G2_TASK"},
    ).allowed
    assert not policy.evaluate(
        "runtime.generate",
        capabilities=generation_capability,
        passed_gates={"G0_ENVIRONMENT", "G1_WORKSPACE"},
    ).allowed
    explicit_policy = PolicyEngine(
        allowed_actions=PolicyEngine.DEFAULT_ALLOWED_ACTIONS | {"runtime.generate"}
    )
    assert explicit_policy.evaluate(
        "runtime.generate",
        capabilities=generation_capability,
        passed_gates={"G0_ENVIRONMENT", "G1_WORKSPACE", "G2_TASK"},
    ).allowed
    assert not policy.evaluate(
        "runtime.generate",
        capabilities={"runtime-generation", "unregistered"},
        passed_gates={"G0_ENVIRONMENT", "G1_WORKSPACE", "G2_TASK"},
    ).allowed


def test_policy_rejects_unsupported_allowlist_entries() -> None:
    with pytest.raises(ValueError, match="unsupported policy actions"):
        PolicyEngine(allowed_actions={"shell.execute"})


def test_agent_capabilities_are_queryable() -> None:
    capabilities = AgentCapabilities(
        (AgentCapability("test-execution", "Run project tests"),)
    )

    assert capabilities.supports("test-execution")
    assert not capabilities.supports("deploy")
    assert capabilities.ids() == ("test-execution",)


def test_agent_capabilities_reject_unregistered_ids() -> None:
    with pytest.raises(ValueError, match="unregistered agent capabilities"):
        AgentCapabilities((AgentCapability("shell-execution", "Run shell commands"),))
