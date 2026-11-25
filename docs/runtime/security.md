# Runtime Security Boundary

The model sees task details and the allowed workspace path. It can return only
patch text; the Agent validates patch paths, blocks secret-file names and symlinks,
and applies the patch with `git apply --check` before applying it.

The Agent does not expose a shell tool, Git remote mutation, push, merge, or
deployment operation. Tests execute repository code inside the workspace process
with a reduced environment and timeout. This is path/process scoping, not an OS
sandbox: only run configured repositories whose code and tests you trust.
