# Debian Node Readiness

`drx node readiness` creates a local, read-only preflight report. It does not
contact another host, write to the DevRelay-X database, alter firewall rules,
install packages, start services, call the runtime endpoint, load a model, or
run inference. `--output` is optional and creates a new report file; it refuses
to overwrite an existing file.

The command is implemented for a future, separately authorized probe on the
target node. It has **not** been run on the Debian production node as part of
P3-01 preparation. Running it on a developer workstation describes only that
workstation.

## Generate a report

Use the approved node ID and paths only after access to that node is authorized:

```text
drx node readiness \
  --node-id Node-001 \
  --config /etc/devrelay-x/config.yaml \
  --model-profile /etc/devrelay-x/model-profile.yaml \
  --workspace-root /srv/devrelay-x/workspaces \
  --control-repository /opt/devrelay-x \
  --output ./node-001-readiness.json
```

The output file must not already exist. A report contains a SHA-256 digest over
its canonical JSON body. Preserve the JSON and its digest as deployment evidence.
This is an unkeyed integrity checksum, not a signed attestation. Do not treat
`needs_review` or `blocked` as approval to deploy.

The optional model profile is YAML with only these fields:

| Field | Meaning |
|---|---|
| `profile_id` | Approved, immutable profile identifier (not a model vendor default) |
| `parameters_billions` | Parameter count in billions |
| `weight_bits` | Effective weight precision |
| `context_tokens` | Context size for the estimate |
| `layers` | Transformer layer count |
| `kv_heads` | Number of key/value heads |
| `head_dimension` | Per-head dimension |
| `kv_bits` | KV-cache precision |
| `runtime_overhead_mib` | Additional runtime memory allowance |
| `weight_overhead_ratio` | Allowance for quantization metadata |
| `safety_margin_ratio` | Additional headroom applied to the estimate |

Supply these values from the actual model/runtime metadata. The estimate assumes
a single sequence and one GPU; it does not claim support for CPU offload,
multi-GPU sharding, runtime-specific kernels, or concurrent requests. Missing
profiles remain `not_assessed`; no model, vendor, or GPU is selected by default.

## Checks and interpretation

The report records Debian release, architecture, CPU, Python, RAM, free disk,
NVIDIA device name/driver/VRAM, `nvidia-smi` driver CUDA compatibility, optional
`nvcc` Toolkit version, and an optional PyTorch CUDA initialization probe. The
`nvidia-smi` CUDA version is the driver's advertised compatibility level, not
proof that a Toolkit is installed. A PyTorch result applies only to that
installed PyTorch build. No static runtime compatibility matrix is assumed.

It also summarizes TCP listeners, UFW/nftables/iptables default inbound and
reported outbound policies,
workspace ownership/mode and control-repository separation, and presence and
permissions of common Git/SSH credential files. It never reads or emits secret
values. Global Git credential helpers are reported for review; the Agent's Git
environment deliberately excludes global Git configuration and SSH-agent
environment variables, so helper presence does not prove that Agent clones can
authenticate.

`pass`, `warn`, `fail`, and `not_assessed` are per-check results. Overall status
is `ready` only when every required probe passes; unknown or warned checks produce
`needs_review`, and any failed check produces `blocked`. The report is
conservative: firewall rules and TCP listeners are summarized rather than fully audited,
UDP listeners and cloud firewall rules are not inspected, and
runtime health/inference compatibility remain unverified unless separately
tested under an approved task.

The probe invokes only local read commands. The PyTorch check may initialize a
CUDA driver context briefly, but it does not allocate model weights or execute
inference.
