# DRX-P1-01 Architecture

The Control Plane owns projects, tasks, run states, persistence and gate decisions.
The Local Agent coordinates a run but cannot declare PASS. It calls a Model Runtime
interface, asks for a unified diff, and never gives the model an arbitrary shell.

The Workspace Manager clones a configured repository to a unique managed path
outside the DevRelay-X control repository by default. Git evidence and Python test
results are collected from that cloned workspace. Evidence and per-gate decisions
are persisted before the task reaches a terminal state.

Implementation boundaries are explicit: `drx/agent` owns the LocalCodingAgent,
AgentRun and capability model; `drx/runtime` contains the production ModelRuntime
interface, configuration and HTTP adapter; `drx/workspace` owns managed Git
workspaces; `drx/execution` builds prompts and runs tests; and `drx/evidence`
contains execution, test and Git evidence types. Test-only runtimes live under
`tests/` and are not part of the production package.

`PolicyEngine` denies unknown actions and accepts only its explicit local
execution allowlist. The LocalCodingAgent checks policy before runtime,
workspace, patch, test and Git operations; the Gate Engine decides from persisted
execution evidence rather than the Agent's completion claim.

```text
Control Plane -> LocalCodingAgent -> ModelRuntime
     |                  |                 |
     |                  +-> Workspace -> TestRunner
     |                         |             |
     +-> SQLite <- Evidence <- Git diff / test result
                  Gate Engine -> PASS / FAIL / BLOCKED
```

When local commits are enabled, a passing test run is committed inside the managed
workspace and the resulting commit is included in Git evidence. The agent never
pushes, merges, or deploys.
