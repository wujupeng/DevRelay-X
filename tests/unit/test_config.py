import pytest
from pydantic import ValidationError

from drx.runtime.config import RuntimeConfig


def test_runtime_requires_loopback_endpoint_and_explicit_model() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        RuntimeConfig(endpoint="https://example.com/v1/chat/completions", model="configured")
    with pytest.raises(ValidationError, match="must be configured"):
        RuntimeConfig(endpoint="http://127.0.0.1:8080/v1/chat/completions", model=" ")


def test_runtime_rejects_credentials_embedded_in_endpoint() -> None:
    with pytest.raises(ValidationError, match="credentials"):
        RuntimeConfig(
            endpoint="http://user:password@127.0.0.1:8080/generate",
            model="configured",
        )
    with pytest.raises(ValidationError, match="credentials"):
        RuntimeConfig(
            endpoint="http://127.0.0.1:8080/generate?token=secret",
            model="configured",
        )
