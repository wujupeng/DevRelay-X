# Local Agent E2E

The integration test `tests/integration/test_local_agent_e2e.py` creates a real
local Git repository from the calculator fixture. A deterministic test runtime
returns a patch based on the cloned source; the production path then validates and
applies that patch, executes the fixture's real pytest suite, creates a local
workspace commit, records Git/test evidence, and runs all seven gates. A separate case verifies actual test failure
produces Gate FAIL. The deterministic runtime is defined only in the test module;
production uses the configurable loopback HTTP adapter under `drx/runtime`.
Another integration case removes `runtime.generate` from the allowlist and proves
that policy denial blocks the AgentRun before generation is called. These tests
do not create production acceptance records.

`tests/integration/test_live_agent_e2e.py` is an opt-in real-model test. Set
`DRX_LIVE_RUNTIME_ENDPOINT` to a loopback-compatible chat-completion URL and
`DRX_LIVE_MODEL_ID` to the served model ID, then run
`pytest -q tests/integration/test_live_agent_e2e.py`. It skips when either
variable is unset. The test verifies a real model-generated change, pytest,
local commit, Evidence, and all seven Gate results; keep the runtime on loopback
and use an SSH tunnel when it runs on another host.
