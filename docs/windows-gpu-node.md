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

The installer requires Python 3.12 or later and installs the project into a
private venv under `%LOCALAPPDATA%\DevRelayX\WindowsGpuNode`. It does not install
Python; `pip` installs the project's declared package dependencies and may need
network access. No model is downloaded. The installer creates a random Bearer
token in `agent.token`, restricts that file to the current user, and does not
print or log the token. Preserve the token securely; do not copy it into issue
reports or logs. `config.yaml` is created from the example only once so an
upgrade does not overwrite local settings.

The default address is `127.0.0.1:8765`; LAN binding is rejected by config
validation. All endpoints require a Bearer token and an allowed source address.
TLS certificate/key settings are supported by the CLI, but TLS is not needed
for loopback-only use. Do not change the bind address or open a firewall port in
P1.

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
size-limited, queued with one active worker, bounded by configured timeouts, and
deduplicated by `request_id`. No shell, arbitrary file, proxy, or Ollama route is
provided. GPU metrics are live `nvidia-smi` observations when available; model
token counts and mock-runtime resource usage remain unavailable.

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
