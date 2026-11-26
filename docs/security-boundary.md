# Security Boundary

External Agent adapters are untrusted. Their output enters DevRelay-X as evidence
and policy input, never as an implicit authority. Repository mutation, credential
access, and policy decisions require explicit boundaries and are not granted to
the base adapter.

Policy denies actions without a registered capability and satisfied prerequisite
gates. Model generation, diff application, and Git commits are high-risk actions
and are disabled unless explicitly enabled in the configured action allowlist.
SQLite enforces Task state transitions as well as ORM-level validation; only a
verified Gate receipt may use the application API to advance a verifying Task
to PASS. The database trigger also requires a completed AgentRun and exactly
seven persisted passing Gate records with a passing execution-evidence record.
