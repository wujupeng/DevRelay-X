# Execution Evidence

Each AgentRun stores task/run/agent/runtime identities, workspace, timestamps,
initial/final HEAD, branch, status, changed paths, diff/statistics, test command,
exit code, captured output/duration, execution result, failure reason, state
transitions and gate results.

Evidence comes from the actual local process and Git repository. Sensitive-looking
values are redacted before persistence; `.env`, private-key files, and credential
URLs are blocked at the workspace/patch boundary. Evidence is associated with its
Task and Run IDs. Node hardware snapshots use a separate node-evidence table.
