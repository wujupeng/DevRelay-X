# Windows GPU Node Agent (P1)

The P1 WindowsGpuNode is a separate, mock-only service. It does not change the
DevRelay-X Local Runtime and does not call Ollama, download models, or perform
GPU inference. Every mock result says `MOCK ONLY`; token and resource usage are
reported as unavailable.

## Install and run

Run PowerShell as the intended standard Windows user, not as Administrator:

```powershell
.\scripts\windows_gpu_node\Install-WindowsGpuNode.ps1
.\scripts\windows_gpu_node\Start-WindowsGpuNode.ps1
.\scripts\windows_gpu_node\Get-WindowsGpuNodeStatus.ps1
.\scripts\windows_gpu_node\Stop-WindowsGpuNode.ps1
```

The installer requires Python 3.12 or later and `uv` already available on PATH.
It installs the project into a private venv under
`%LOCALAPPDATA%\DevRelayX\WindowsGpuNode` from the committed `uv.lock` using
frozen, no-dev synchronization. It does not install Python or uv. Dependency
downloads may require network access; no model is downloaded. The installer
creates a random Bearer token in `agent.token`, restricts that file to the
current user, and does not print or log the token. Preserve the token securely;
do not copy it into issue reports or logs. `config.yaml` is created from the
example only once so an upgrade does not overwrite local settings.

The default address is `127.0.0.1:8765`; LAN binding is rejected by config
validation. All endpoints require a Bearer token and an allowed source address.
For explicitly authorized cross-host testing only, the separate LAN example
requires all three settings: a specific non-loopback bind IP, `enable_lan_access:
true`, a single-host non-loopback source allowlist, and a TLS certificate/key
pair. Binding to `0.0.0.0`, a subnet-wide allowlist, or cleartext LAN HTTP is
rejected. The default configuration remains loopback-only.

To start on machine boot without storing a password, opt in explicitly:

```powershell
.\scripts\windows_gpu_node\Install-WindowsGpuNode.ps1 -RegisterStartupTask
```

This registers an AtStartup task for the current user with S4U logon and limited
privileges. It does not request elevation or grant rights. If Windows rejects
the task or requires elevation, stop and ask an administrator to review; do not
run the Agent elevated. Verify the task and service with
`Get-WindowsGpuNodeStatus.ps1`.

For an RDP-disconnect check, register the startup task, start the Agent, confirm
the status script reports `healthy`, disconnect the RDP client without signing
out, then reconnect and run the status script again. A stronger cold-start
check is to reboot while no interactive session is open and verify the task
starts the Agent. Save the two status outputs and task history as evidence.
These checks must be performed on the target Windows host; local unit tests do
not prove RDP-disconnect behavior.

## Authorized Debian cross-host test (P1.5)

The separate deployment and test checklist is in
[remote GPU node acceptance](./remote-gpu-node-acceptance.md). Use an
organization-approved certificate authority to issue a server certificate
whose subject alternative name includes `IP:192.168.2.85`. Keep the private key
on Windows and provide Debian only the issuing CA certificate/chain. Do not
disable client certificate verification or use a self-signed leaf certificate
without an approved trust procedure.

`Set-WindowsGpuNodeFirewall.ps1` is the only script in this package that
requires elevation. It creates one TCP allow rule restricted to the configured
Debian source IP, Windows local IP, Agent port, executable, and Domain/Private
profiles; `-Remove` is its rollback. It does not create an Ollama rule. The
Windows Agent must separately be configured with the explicit LAN settings
above. Review and verify both layers before starting the process.

## API

The service exposes only the following authenticated routes:

- `GET /api/v1/health`
- `GET /api/v1/capabilities`
- `GET /api/v1/metrics`
- `GET /api/v1/models`
- `POST /api/v1/inference`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `POST /api/v1/node/drain`
- `POST /api/v1/node/resume`

Inference accepts only the configured `mock-only-v1` model. Requests are
size-limited, rate-limited per allowed source, queued with one active worker,
bounded by configured timeouts, and deduplicated by `request_id`. No shell,
arbitrary file, proxy, or Ollama route is provided. GPU metrics are live
`nvidia-smi` observations when available; model token counts and mock-runtime
resource usage remain unavailable.

Application logs rotate at the configured size and count under the local
`logs` directory. They contain lifecycle messages but never request bodies,
authorization headers, or the token. The lifecycle scripts maintain a local PID
file and timestamped process stdout/stderr files.

## Scope and known limitations

- P1 is not a production deployment or LAN-accessible node.
- Real model execution, Ollama integration, remote Debian connectivity, TLS
  provisioning, firewall changes, and node registration are out of scope.
- RDP-disconnect and startup-task behavior require validation on the target
  Windows host and are not established by repository tests.
