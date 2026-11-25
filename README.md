# DevRelay-X

DevRelay-X v0.1 is an AI software delivery relay and governance platform.

It treats Git commits, test output, and agent declarations as evidence inputs.
Final task state is determined by the Gate Engine from verifiable evidence.

## Requirements

- Python 3.12+
- SQLite

## Install

```powershell
py -3.12 -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -e ".[dev]"
```

## Run the API

```powershell
python -m uvicorn drx.api:app --reload
```

The foundation currently exposes `GET /health` and `GET /` but does not include the final UI.

## CLI

```powershell
drx status
drx init
```

## Test

```powershell
pytest
ruff check .
```
