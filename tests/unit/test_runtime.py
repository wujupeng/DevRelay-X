import json

import httpx

from drx.runtime.config import RuntimeConfig
from drx.runtime.http import LocalHTTPRuntime


def test_http_runtime_generation_stream_and_health() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(204)
        payload = json.loads(request.content)
        if payload["stream"]:
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"content":"part"}}]}\n\n'
                    "data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "patch"}}]},
        )

    runtime = LocalHTTPRuntime(
        RuntimeConfig(
            endpoint="http://127.0.0.1:8080/v1/chat/completions",
            model="configured-model",
        )
    )
    runtime._client = httpx.Client(transport=httpx.MockTransport(respond))
    assert runtime.health_check()
    assert runtime.generate("prompt") == "patch"
    assert list(runtime.stream("prompt")) == ["part"]
    assert runtime.identify()["model"] == "configured-model"
    runtime.shutdown()
