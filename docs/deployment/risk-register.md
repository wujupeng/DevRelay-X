# Debian Node Deployment Risk Register

| Risk | Current evidence | Mitigation / exit criterion |
|---|---|---|
| Target hardware identity is unverified | No probe was run on the production Debian node; the “GTX1060S” label may not match the device name reported by the driver | Capture node-local readiness report and confirm exact SKU and VRAM with PM |
| Driver/Toolkit/runtime compatibility is unknown | The read-only CLI smoke probe ran on the Windows developer workstation; no target Debian runtime was tested | Verify actual driver/runtime versions and runtime-specific support matrix before any model task |
| Model may exceed free VRAM | No approved model profile or target free-memory snapshot exists | Supply exact model geometry, quantization, context, runtime overhead, and concurrency assumptions; review estimate and runtime benchmark separately |
| API has no authentication | Current API exposes `/` and `/health` only and has no auth layer | Bind loopback; do not expose via LAN or unauthenticated reverse proxy |
| Git credentials may not reach Agent subprocesses | Git subprocess environment omits global Git config and SSH-agent variables | Use a dedicated service account and explicitly validated repository-scoped key/credential flow; no secret values in URLs/config evidence |
| Workspace data may be exposed or share control files | Workspace manager creates paths but host ownership/modes depend on deployment setup | Dedicated owner, separate roots, mode `0700`, verify control repo exclusion |
| Firewall status is not equivalent to a full network audit | Probe checks listener binds and summarizes firewall default policy only | Manually review inbound allowlist, SSH source ranges, routes, and host/cloud firewall before authorization |
| Task retry could duplicate changes | Agent execution is CLI-based; no recovery queue or safe automatic replay | Keep task unit `Restart=no`; preserve failed Run/Evidence/workspace for human review |
| SQLite local-file access exceeds trigger boundary | Database triggers do not protect against a privileged operator replacing the DB or dropping schema triggers | Restrict filesystem access and backups; treat host administrators as trusted |
| Systemd hardening may block GPU or Git operations | No production unit has been installed or tested | Test least-privilege unit in an isolated staging environment before deployment approval |

**Status:** P3 deployment remains on HOLD. This register is preparation evidence,
not a deployment authorization or a claim that the target node passed checks.
