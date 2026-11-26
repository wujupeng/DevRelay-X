# Debian Node Deployment Plan

## Approval boundary

P3-01 prepares deployment; it does not authorize or perform deployment. No
production-node access, package installation, service start, firewall change,
runtime installation, or model task was performed while preparing this plan.
Every command in this document is for a later deployment window approved by PM.

The current FastAPI application exposes only `/` and `/health`; it has no
authentication and no task-execution API. Bind it to loopback only. Do not expose
it on a LAN/public interface or place it behind an unauthenticated proxy. If
remote operator access is later approved, use a controlled SSH tunnel; a reverse
proxy requires a separately approved authentication and access-control design.
The current Agent execution path is the CLI, not an API queue.

## Proposed host layout

- Dedicated unprivileged `devrelay` service account; no shared developer account.
- Application release: `/opt/devrelay-x` (read-only to the service account).
- State and SQLite database: `/var/lib/devrelay-x`.
- Workspace root: `/srv/devrelay-x/workspaces`, owned by `devrelay`, mode `0700`.
- Configuration: `/etc/devrelay-x/config.yaml`, owned by root and readable only
  by the service account (`0640` or stricter).
- SSH keys, if required, are repository-scoped deploy keys owned by `devrelay`,
  mode `0600`; `.ssh` directory mode `0700`. Do not copy personal keys.
- No credentials in Git URLs, report files, or model profiles. Current runtime
  adapter sends no authentication headers. Do not add a remote runtime endpoint
  or secret-handling mechanism without a separate design and approval.

Use the readiness report as a checklist, not as an automatic deploy gate.
Confirm the exact Debian release, GPU SKU/VRAM, driver, runtime, workspace path,
firewall policy, Git transport, disk/RAM thresholds, and model profile with PM.
The label “GTX1060S” is not treated as verified hardware identity; the probe
records the device reported by the host.

## Service lifecycle

The example units in [`examples/`](./examples/) are templates only; they have
not been installed. The API unit binds to `127.0.0.1:8010`. It does not execute
tasks. A task instance is a separate one-shot process with automatic restart
disabled, to avoid silently re-running code changes after a crash.

After separate deployment approval, an operator would review the unit files,
install the approved release and configuration, then use:

```text
systemctl daemon-reload
systemctl enable --now devrelay-x-api.service
systemctl status devrelay-x-api.service
systemctl stop devrelay-x-api.service
```

One-shot task instances are started and inspected individually:

```text
systemctl start devrelay-x-task@<task-id>.service
systemctl status devrelay-x-task@<task-id>.service
systemctl stop devrelay-x-task@<task-id>.service
```

Do not enable automatic task retries. A stopped or failed run must be treated as
incomplete; inspect its Task/Run/Evidence and retained workspace before a human
decides whether to create a new task. The current CLI/API does not provide an
automatic recovery queue.

## Startup checks

1. Capture and review the readiness JSON and SHA-256 digest.
2. Verify service-user ownership/modes and firewall default-deny inbound policy.
3. Confirm only loopback listeners are present; do not infer safety from a
   successful `/health` response.
4. Confirm the configured HTTP runtime endpoint is loopback-only. A runtime
   health check is a separate, explicitly approved step; this preparation sends
   no request to it.
5. Start only the API health service first. Verify `/health` from the node, then
   stop it if further task/runtime acceptance has not been approved.
6. Do not launch an Agent task or model until separate P3/P4 authorization.

## Service recovery

For an API process failure, inspect `systemctl status` and the journal, preserve
the readiness evidence, and restart only the API service after checking the
cause. For a task process failure, do not auto-restart: preserve the database,
Evidence and workspace, record the failure, and require operator review.

Use [`rollback.md`](./rollback.md) for rollback steps. Service account, model
runtime, GPU driver, kernel, firewall, and application release changes must be
planned as separate reversible changes.
