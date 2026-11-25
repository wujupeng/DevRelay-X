from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from drx.runtime.config import RuntimeConfig


class LocalHTTPRuntime:
    """Adapter for local runtimes exposing a configurable chat-completion endpoint."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self._client = httpx.Client(
            timeout=config.timeout_seconds,
            trust_env=False,
        )

    def identify(self) -> dict[str, str]:
        return {"type": self.config.type, "model": self.config.model}

    def capabilities(self) -> list[str]:
        return ["text-generation", "streaming"]

    def health_check(self) -> bool:
        endpoint = urlsplit(self.config.endpoint)
        try:
            response = self._client.get(f"{endpoint.scheme}://{endpoint.netloc}/")
        except httpx.HTTPError:
            return False
        return response.status_code < 500

    def _request_body(self, prompt: str, *, stream: bool = False) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": stream,
        }

    def generate(self, prompt: str) -> str:
        response = self._client.post(
            self.config.endpoint, json=self._request_body(prompt)
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("runtime returned an invalid completion response") from error
        if not isinstance(content, str) or not content:
            raise ValueError("runtime returned an empty completion")
        return content

    def stream(self, prompt: str) -> Iterator[str]:
        with self._client.stream(
            "POST",
            self.config.endpoint,
            json=self._request_body(prompt, stream=True),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = httpx.Response(200, text=data).json()
                    text = chunk["choices"][0]["delta"].get("content", "")
                except (KeyError, IndexError, TypeError, ValueError) as error:
                    raise ValueError("runtime returned an invalid stream chunk") from error
                if text:
                    yield str(text)

    def estimate_capability(self, task: str) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "estimated_input_characters": len(task),
            "requires_model_judgement": True,
        }

    def shutdown(self) -> None:
        self._client.close()
