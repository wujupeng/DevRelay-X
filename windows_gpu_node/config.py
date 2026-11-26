import ipaddress
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(default="Node-002", min_length=1, max_length=64)
    bind_host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    allowed_sources: list[str] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )
    token_env_var: str = "DRX_GPU_NODE_TOKEN"
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None
    log_file: Path = Path("logs/windows-gpu-node.log")
    log_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    log_backup_count: int = Field(default=5, ge=1, le=20)
    max_queue_size: int = Field(default=8, ge=1, le=128)
    max_request_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    max_prompt_chars: int = Field(default=32_000, ge=1, le=64_000)
    max_system_prompt_chars: int = Field(default=4_000, ge=0, le=16_000)
    max_job_timeout_seconds: int = Field(default=300, ge=1, le=1800)
    mock_model_id: str = Field(default="mock-only-v1", min_length=1, max_length=128)
    mock_delay_seconds: float = Field(default=0, ge=0, le=30)

    @field_validator("bind_host")
    @classmethod
    def require_loopback_bind_host(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError("bind_host must be a loopback IP address") from error
        if not address.is_loopback:
            raise ValueError("LAN binding is disabled in this release")
        return str(address)

    @field_validator("allowed_sources")
    @classmethod
    def require_single_host_allowlist(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("allowed_sources must contain at least one address")
        normalized: list[str] = []
        for value in values:
            try:
                network = ipaddress.ip_network(value, strict=False)
            except ValueError as error:
                raise ValueError("allowed_sources entries must be valid IP addresses") from error
            if network.prefixlen != network.max_prefixlen:
                raise ValueError("allowed_sources entries must be single-host addresses")
            normalized.append(str(network))
        return normalized

    @field_validator("token_env_var")
    @classmethod
    def validate_token_env_var(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
            raise ValueError("token_env_var must be an uppercase environment variable name")
        return value

    @model_validator(mode="after")
    def require_tls_pair(self) -> "NodeConfig":
        if (self.tls_cert_file is None) != (self.tls_key_file is None):
            raise ValueError("tls_cert_file and tls_key_file must be configured together")
        return self

    def permits_source(self, address: str | None) -> bool:
        if address is None:
            return False
        try:
            client_address = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(
            client_address in ipaddress.ip_network(source)
            for source in self.allowed_sources
        )


def load_config(path: str | Path) -> NodeConfig:
    config_path = Path(path)
    try:
        content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Unable to read node config: {config_path}") from error
    if not isinstance(content, dict):
        raise ValueError("Node config must be a YAML mapping")

    for key in ("log_file", "tls_cert_file", "tls_key_file"):
        if key in content and content[key] is not None:
            content[key] = Path(os.path.expandvars(str(content[key]))).expanduser()
    return NodeConfig.model_validate(content)


def load_token(config: NodeConfig) -> str:
    token = os.environ.get(config.token_env_var, "")
    if len(token) < 32:
        raise ValueError(
            f"{config.token_env_var} must contain a bearer token of at least 32 characters"
        )
    return token
