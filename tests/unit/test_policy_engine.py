import pytest

from drx.agent.capabilities import AgentCapabilities, AgentCapability
from drx.policy.engine import PolicyEngine


def test_policy_allows_explicit_default_action_and_denies_unknown_actions() -> None:
    policy = PolicyEngine()

    assert policy.evaluate("workspace.create").allowed
    assert policy.evaluate("git.commit").allowed
    assert not policy.evaluate("shell.execute").allowed
    assert not policy.evaluate("git.push").allowed
    assert not policy.evaluate("").allowed


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
