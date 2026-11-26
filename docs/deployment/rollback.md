# Debian Node Rollback Plan

This is a future operator procedure for a separately approved deployment. It
has not been executed.

## Before a change

1. Record the deployed release SHA, readiness report, and report digest.
2. Stop task admission and confirm no Agent task is running.
3. Stop the API service and any explicitly approved inference service.
4. Take a consistent backup of the SQLite database and preserve workspaces and
   Git commit IDs. Do not overwrite or reset task workspaces during rollback.
5. Record current firewall, systemd unit, runtime, driver, and model settings.

## Roll back an application release

1. Stop `devrelay-x-api.service`; verify it is inactive.
2. Restore the previous immutable application release and its matching Python
   environment. Do not edit the live release in place.
3. Restore the pre-change configuration and service unit from the reviewed
   backup; verify ownership and restrictive permissions.
4. If the schema changed, restore the matching database backup rather than
   attempting an unreviewed downgrade. Keep the failed database copy for audit.
5. Start the previous API release only after a read-only status check, then
   verify local `/health` and the expected release SHA.
6. Keep task execution disabled until the failure and any partially changed
   workspaces have been reviewed.

## Roll back a driver/runtime change

Driver and inference-runtime changes can affect the kernel, CUDA libraries,
device access, and model compatibility. Use the host vendor's approved package
rollback procedure; do not remove or replace packages ad hoc. Reboot only during
an approved maintenance window. Re-run the read-only readiness probe and require
PM review before launching any model or Agent task.

## Data integrity

Keep the original database and workspace snapshots immutable for investigation.
Do not claim a Task PASS based on a Git commit or restored database alone; the
Gate/Evidence checks remain authoritative. If recovery state is ambiguous, keep
the task blocked and escalate rather than replaying it automatically.
