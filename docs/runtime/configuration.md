# Model Runtime

`ModelRuntime` separates identity, capabilities, health, generation, streaming,
capability estimates and shutdown from the Local Agent. The first adapter is a
generic configurable HTTP chat-completion adapter; it has no fixed model/vendor.

Copy `config.example.yaml` to `.drx/config.yaml`, set `runtime.model` and the local
endpoint path, and ensure the runtime listens on loopback. URLs containing
credentials and non-loopback hosts are rejected. The adapter sends no auth headers
and never writes model configuration into evidence except the configured model ID.

`drx runtime list` prints the configured identity. `drx runtime health` checks
connectivity; a successful health request does not guarantee generation works.
Generation output must be a valid workspace-relative unified diff.
