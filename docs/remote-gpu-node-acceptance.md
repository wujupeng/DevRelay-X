# DRX-P3-RTX-A400-02 — Deployment and cross-host acceptance

This is an operator procedure for the explicitly authorized isolated
P1-DEPLOY/P1.5 test. It is not production scheduling authorization. The
acceptance harness runs only synthetic prompts against the Mock Runtime; it
does not install Ollama, download a model, run GPU inference, or modify driver
or CUDA settings.

## Prerequisites and certificate trust

1. Confirm the Windows node is `airplm` / `192.168.2.85`, the Debian node is
   `192.168.2.3`, and both are on the intended private network.
2. Obtain an organization-approved CA-issued server certificate with
   `IP:192.168.2.85` in its SAN and server-auth usage. Copy its private key only
   to the Windows node. Transfer only the CA certificate/chain to Debian.
3. If an approved CA is unavailable, stop and request PKI approval. Do not use
   HTTP, `verify=False`, a trust-all context, or an unapproved leaf certificate
   to get past the block.
4. The Windows configuration must use the exact node address and source
   allowlist shown below. Never bind to `0.0.0.0`.

## Windows operator steps

Run setup as the intended standard user, not Administrator. From a checked-out
copy at the committed SHA:

```powershell
.\scripts\windows_gpu_node\Install-WindowsGpuNode.ps1
Copy-Item .\windows_gpu_node\config.lan.example.yaml "$env:LOCALAPPDATA\DevRelayX\WindowsGpuNode\config.yaml" -Force
```

Confirm the certificate and key paths in that config. Place the CA-issued
certificate chain and private key in the configured directory. The private key
must remain on Windows and have a standard-user-only ACL:

```powershell
.\scripts\windows_gpu_node\Prepare-WindowsGpuNodeTlsKey.ps1
```

The Agent token is randomly created at
`%LOCALAPPDATA%\DevRelayX\WindowsGpuNode\agent.token`; it is not printed. Keep
it on the Windows host, and securely provision the same token into Debian's
environment. Do not paste it into chat, a Git file, logs, or command-line
arguments.

Register and run the non-elevated startup task:

```powershell
.\scripts\windows_gpu_node\Register-WindowsGpuNodeStartupTask.ps1
.\scripts\windows_gpu_node\Start-WindowsGpuNode.ps1
.\scripts\windows_gpu_node\Get-WindowsGpuNodeStatus.ps1
```

The task uses S4U logon and limited privileges, without storing a password. If
Task Scheduler requires elevation or refuses S4U, stop and have the Windows
administrator review that specific policy; do not run the Agent as
Administrator or SYSTEM.

Only the following firewall operation is elevated. An administrator should
review the parameters, then run in an elevated PowerShell:

```powershell
$agentPython = "C:\Users\<agent-user>\AppData\Local\DevRelayX\WindowsGpuNode\.venv\Scripts\python.exe"
.\scripts\windows_gpu_node\Set-WindowsGpuNodeFirewall.ps1 -PythonExe $agentPython
Get-NetFirewallRule -DisplayName DevRelayX-WindowsGpuNode-HTTPS
Get-NetFirewallPortFilter -AssociatedNetFirewallRule (Get-NetFirewallRule -DisplayName DevRelayX-WindowsGpuNode-HTTPS)
Get-NetFirewallAddressFilter -AssociatedNetFirewallRule (Get-NetFirewallRule -DisplayName DevRelayX-WindowsGpuNode-HTTPS)
```

Rollback the rule with:

```powershell
.\scripts\windows_gpu_node\Set-WindowsGpuNodeFirewall.ps1 -Remove
```

No inbound rule for TCP 11434 is created. Verify that no other pre-existing
rule exposes it before acceptance. Record the Windows identity, PID, start
time, listener address/port, TLS certificate thumbprint/SAN/validity, firewall
rule filters, and status output. Do not include private key material or token.

## Debian adapter install and online test

Copy the CA certificate/chain to `/etc/devrelay-x/windows-gpu-node/` and protect
it and the adapter configuration with root ownership. Use a protected
environment file (mode `0600`) for the token variable
`DRX_REMOTE_GPU_NODE_TOKEN`; do not place its value in YAML, shell history,
process arguments, or the evidence report. The same protected env file should
set the non-secret `AGENT_COMMIT_SHA` from the reviewed release record. The
example adapter configuration is
[config.remote_gpu_node.example.yaml](../config.remote_gpu_node.example.yaml).
The adapter always verifies HTTPS using that CA bundle and sets
`trust_env=False`; there is no configuration to disable verification.

In the deployed repository at the reviewed commit, use the same lock on Debian
(the node currently reports Python 3.13.5, which satisfies the project floor):

```sh
cd /home/debian/DevRelay-X
uv sync --project /home/debian/DevRelay-X --frozen --no-dev --python 3.13
set -a
. /etc/devrelay-x/windows-gpu-node/adapter.env
set +a
/home/debian/DevRelay-X/.venv/bin/python scripts/debian_remote_gpu_node_acceptance.py \
  --config /etc/devrelay-x/windows-gpu-node/adapter.yaml \
  --expected-agent-commit-sha "$AGENT_COMMIT_SHA" \
  --untrusted-ca-bundle /etc/devrelay-x/windows-gpu-node/unrelated-valid-ca.pem \
  --output /home/debian/p1.5-evidence/windows-gpu-node-acceptance.json
```

Run the probe with the protected environment file loaded. It records UTC time,
test hostname, checked-out Git SHA, health/capability/metrics/model responses,
missing/wrong-token HTTP status, RTX A400 hardware fields, and 20 sequential
synthetic Mock jobs with request IDs, job IDs, status, mock response, and
elapsed time. It checks TCP 11434 from Debian. If a separate, intentionally
untrusted CA bundle is available, pass it through `--untrusted-ca-bundle`; the
full online acceptance requires this check to pass. No token is written to the
report. Set `AGENT_COMMIT_SHA` to the reviewed SHA before running; the command
does not pass the Bearer token as an argument.

The Windows example config uses a two-second Mock delay to make the cancellation
and remote-timeout cases observable, and a bounded request-rate allowance sized
for this 20-job probe. A successful Mock response is only proof of the
authenticated HTTP call chain; it is not GPU inference.

## Offline, restart, and RDP evidence

While the Windows Agent is intentionally stopped, run:

```sh
/home/debian/DevRelay-X/.venv/bin/python scripts/debian_remote_gpu_node_acceptance.py \
  --config /etc/devrelay-x/windows-gpu-node/adapter.yaml \
  --output /var/log/devrelay-x/windows-gpu-node-offline.json \
  --expect-offline
```

The offline probe does not require `--expected-agent-commit-sha`; the online
probe requires it and verifies that the installed Windows Agent reports the
same reviewed SHA. The report therefore records and checks both the Debian
checkout SHA and Windows Agent SHA.

The report must show an explicit connection failure and must not show a
success-shaped model result. Restart the Agent, rerun the online probe, and
record recovery. For RDP validation, capture Debian health/capability checks,
disconnect RDP without logging out, wait at least five minutes, repeat Debian
checks, reconnect, then compare the Windows PID/start time and status. Retain
test outputs with commit SHA and timestamps. Do not claim RDP independence
without the target-host evidence.

Network allowlist enforcement and the TCP 11434 policy require Windows
firewall-rule inspection and a test from an actually unauthorized source. A
Debian-only local unit test cannot prove those host/network controls.

## Failure handling and rollback

- Wrong token must return 401; missing token must return 401.
- An untrusted or hostname/IP-mismatched certificate must fail TLS.
- Agent unavailability must raise a connection error; jobs are not retried or
  redirected to another node.
- Caller timeout requests cancellation and reports timeout; it does not invent
  output.
- Stop the Agent with `Stop-WindowsGpuNode.ps1`, remove its scheduled task using
  `Unregister-ScheduledTask -TaskName DevRelayX-WindowsGpuNode -Confirm:$false`,
  then remove the scoped firewall rule. Preserve evidence and logs according to
  local policy. No runtime/model data is altered by this rollback.
