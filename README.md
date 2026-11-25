# DevRelay-X

DevRelay-X v0.1 is an AI software delivery relay and governance platform. It runs
local coding tasks in isolated Git workspaces and determines run/task outcomes
from persisted execution evidence. Agent output, commits, and a successful test
command do not independently declare a task accepted.

## Requirements

- Python 3.12+
- Git
- A local HTTP-compatible LLM runtime exposing a chat-completion endpoint
- SQLite (included with Python)

## Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item config.example.yaml .drx\config.yaml
```

Edit `.drx/config.yaml` to set the actual local runtime endpoint and model ID.
The adapter only accepts loopback HTTP(S) endpoints and does not store API keys.
The `.drx/` directory is ignored by Git.

## Local Agent Flow

Create a task that points to a Git repository:

```powershell
drx init
drx task create DRX-DEMO-001 `
  --project-id demo --project-name Demo `
  --repository C:\path\to\your\git\repository `
  --title "Implement example" `
  --objective "Implement the requested change" `
  --acceptance-criterion "Configured tests pass"
drx runtime health
drx agent run DRX-DEMO-001
```

The Agent receives only a deterministic task prompt and returns a unified diff.
DevRelay-X validates/applies it, runs detected Python tests, collects Git state,
persists evidence, and invokes the Gate Engine:

```powershell
drx run show <run-id>
drx run evidence <run-id>
drx gate show <run-id>
```

No automatic Git commit, push, merge, or deployment is performed. Workspaces are
created under `~/.devrelay-x/workspaces` by default and retained for review.

## API and Node

```powershell
python -m uvicorn drx.api:app --reload
drx node health
drx node list
```

The v0.1 API currently exposes `GET /` and `GET /health`. `drx node list`
captures hardware details as `Node-001` evidence; GPU discovery is optional.

## Development

```powershell
pytest
ruff check .
mypy drx
```

See [architecture](docs/architecture/overview.md), [local node](docs/local-node/setup.md),
[runtime](docs/runtime/configuration.md), [evidence](docs/evidence/model.md), and
[gates](docs/gates/engine.md) for boundaries, configuration and troubleshooting.
