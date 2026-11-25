from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["http_compatible"] = "http_compatible"
    endpoint: str
    model: str
    timeout_seconds: float = Field(default=120, gt=0, le=1800)
    max_tokens: int = Field(default=4096, gt=0, le=32768)
    temperature: float = Field(default=0.1, ge=0, le=2)

    @field_validator("endpoint")
    @classmethod
    def require_loopback_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("runtime endpoint must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("credentials must not be embedded in runtime URLs")
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("runtime endpoint must use a loopback host")
        if not parsed.path:
            raise ValueError("runtime endpoint must include its generation path")
        return value

    @field_validator("model")
    @classmethod
    def require_model_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("runtime model must be configured")
        return value.strip()
