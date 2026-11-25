# Architecture

DevRelay-X separates domain models, persistence, external Agent adapters, policy,
Git operations, and evidence evaluation.

The Gate Engine is the sole component that determines final PASS, FAIL, or BLOCKED
state. Agent declarations and Git commits are evidence inputs, not terminal states.
