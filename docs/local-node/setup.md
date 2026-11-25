# Local Node

`drx node list` records OS, CPU, RAM, disk, Python and best-effort GPU discovery
as `Node-001` evidence in SQLite. NVIDIA data is collected when `nvidia-smi` is
available; missing GPU tools do not block node operation or tests.

Workspace clones live outside the control repository by default. Cleanup is
explicit and only removes a managed direct child with its ownership marker.
Agent-created workspaces are retained by default for human review.

## Troubleshooting

- Ensure Git is on `PATH` and the configured repository is cloneable.
- Do not embed credentials in repository URLs.
- Choose a workspace directory with adequate disk space and write permissions.
