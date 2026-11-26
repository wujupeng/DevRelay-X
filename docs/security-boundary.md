# Security Boundary

External Agent adapters are untrusted. Their output enters DevRelay-X as evidence
and policy input, never as an implicit authority. Repository mutation, credential
access, and policy decisions require explicit boundaries and are not granted to
the base adapter.

Policy denies actions without a registered capability and satisfied prerequisite
gates. Model generation, diff application, and Git commits are high-risk actions
and are disabled unless explicitly enabled in the configured action allowlist.
SQLite enforces Task state transitions as well as ORM-level validation; only a
GateEngine-issued HMAC approval may advance a verifying Task to PASS. A SQLite
validation function checks that approval against the persisted Task, AgentRun,
commit, and execution-evidence fields; the trigger also requires a completed
run and exactly the registered seven passing Gates. Direct SQL cannot insert a
Task already marked PASS. After PASS, the run, evidence, and Gate rows are
immutable through normal database writes.

These SQLite protections assume the database schema and application process
are trusted. They do not protect against an administrator who can replace the
database file or remove its triggers; filesystem access to the database must
remain restricted.
