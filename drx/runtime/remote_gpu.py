import ipaddress
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RemoteGpuNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    ca_bundle_file: Path
    token_env_var: str = "DRX_REMOTE_GPU_NODE_TOKEN"
    expected_node_id: str = "Node-002"
    model_id: str = "mock-only-v1"
    connect_timeout_seconds: float = Field(default=5, gt=0, le=60)
    read_timeout_seconds: float = Field(default=10, gt=0, le=120)
    write_timeout_seconds: float = Field(default=15, gt=0, le=120)
    pool_timeout_seconds: float = Field(default=5, gt=0, le=60)
    poll_interval_seconds: float = Field(default=0.25, gt=0, le=10)
    job_timeout_seconds: int = Field(default=300, ge=1, le=1800)

    @field_validator("endpoint")
    @classmethod
    def require_https_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "remote GPU endpoint must be a base HTTPS URL without credentials or path"
            )
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("remote GPU endpoint must use TCP port 8765") from error
        if port != 8765:
            raise ValueError("remote GPU adapter is restricted to Agent TCP port 8765")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError("remote GPU endpoint must use a private IPv4 address") from error
        if address.version != 4 or not address.is_private or address.is_loopback:
            raise ValueError("remote GPU endpoint must use a private IPv4 address")
        return value.rstrip("/")

    @field_validator("token_env_var")
    @classmethod
    def validate_token_env_var(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
            raise ValueError("token_env_var must be an uppercase environment variable name")
        return value

    @field_validator("ca_bundle_file")
    @classmethod
    def require_ca_bundle_path(cls, value: Path) -> Path:
        if not value.is_file():
            raise ValueError("ca_bundle_file must refer to a trusted CA bundle file")
        return value


def load_remote_gpu_config(path: str | Path) -> RemoteGpuNodeConfig:
    config_path = Path(path)
    try:
        content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Unable to read remote GPU config: {config_path}") from error
    if not isinstance(content, dict):
        raise ValueError("Remote GPU config must be a YAML mapping")
    if "ca_bundle_file" in content:
        content["ca_bundle_file"] = Path(
            os.path.expandvars(str(content["ca_bundle_file"]))
        ).expanduser()
    return RemoteGpuNodeConfig.model_validate(content)


class RemoteGpuNodeError(RuntimeError):
    """Base class for explicit remote-node failures."""


class RemoteGpuConnectionError(RemoteGpuNodeError):
    """The node could not be reached or its TLS connection was rejected."""


class RemoteGpuHttpError(RemoteGpuNodeError):
    def __init__(self, status_code: int, endpoint: str) -> None:
        self.status_code = status_code
        super().__init__(f"Remote GPU node returned HTTP {status_code} for {endpoint}")


class RemoteGpuJobError(RemoteGpuNodeError):
    pass


class RemoteGpuInferenceTimeout(RemoteGpuNodeError):
    pass


class RemoteGpuRuntimeAdapter:
    """Standalone, TLS-verifying adapter for the WindowsGpuNode HTTP API."""

    def __init__(
        self,
        config: RemoteGpuNodeConfig,
        *,
        auth_token: str | None = None,
    ) -> None:
        self.config = config
        token = auth_token if auth_token is not None else os.environ.get(config.token_env_var, "")
        if len(token) < 32:
            raise ValueError(
                f"{config.token_env_var} must contain a bearer token of at least 32 characters"
            )
        self._token = token
        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.write_timeout_seconds,
            pool=config.pool_timeout_seconds,
        )
        self._client = httpx.Client(
            verify=str(config.ca_bundle_file),
            timeout=timeout,
            trust_env=False,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        timeout = None
        if read_timeout_seconds is not None:
            timeout = httpx.Timeout(
                connect=self.config.connect_timeout_seconds,
                read=max(0.001, min(self.config.read_timeout_seconds, read_timeout_seconds)),
                write=self.config.write_timeout_seconds,
                pool=self.config.pool_timeout_seconds,
            )
        try:
            response = self._client.request(
                method,
                f"{self.config.endpoint}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=json,
                timeout=timeout,
            )
        except httpx.HTTPError as error:
            raise RemoteGpuConnectionError(
                f"HTTPS request to remote GPU node failed ({type(error).__name__})"
            ) from error
        if not response.is_success:
            raise RemoteGpuHttpError(response.status_code, path)
        try:
            payload = response.json()
        except ValueError as error:
            raise RemoteGpuNodeError(f"Remote GPU node returned invalid JSON for {path}") from error
        if not isinstance(payload, dict):
            raise RemoteGpuNodeError(f"Remote GPU node returned an invalid object for {path}")
        return payload

    def connect(self) -> dict[str, Any]:
        health = self._request("GET", "/api/v1/health")
        if health.get("node_id") != self.config.expected_node_id:
            raise RemoteGpuNodeError("Connected node identity did not match configuration")
        if health.get("status") != "ok" or health.get("mock_only") is not True:
            raise RemoteGpuNodeError("Remote node is not reporting the expected mock-only state")
        return health

    def health_check(self) -> bool:
        try:
            self.connect()
        except RemoteGpuNodeError:
            return False
        return True

    def get_capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/capabilities")

    def get_metrics(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/metrics")

    def list_models(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/v1/models")
        models = payload.get("models")
        if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
            raise RemoteGpuNodeError("Remote GPU node returned an invalid model list")
        return models

    def submit(
        self,
        prompt: str,
        *,
        timeout_seconds: int | None = None,
        request_id: UUID | None = None,
    ) -> str:
        bounded_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self.config.job_timeout_seconds
        )
        if not 1 <= bounded_timeout <= self.config.job_timeout_seconds:
            raise ValueError("inference timeout must be within the configured job timeout")
        payload = self._request(
            "POST",
            "/api/v1/inference",
            json={
                "request_id": str(request_id or uuid4()),
                "model": self.config.model_id,
                "prompt": prompt,
                "max_tokens": 64,
                "temperature": 0.2,
                "timeout_seconds": bounded_timeout,
            },
        )
        job_id = payload.get("job_id")
        if payload.get("mock") is not True or not isinstance(job_id, str):
            raise RemoteGpuNodeError("Remote node did not acknowledge a mock job")
        try:
            return str(UUID(job_id))
        except ValueError as error:
            raise RemoteGpuNodeError("Remote node returned an invalid job ID") from error

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/jobs/{UUID(job_id)}")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/jobs/{UUID(job_id)}/cancel")

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("job wait timeout must be greater than zero")
        deadline = time.monotonic() + (
            timeout_seconds
            if timeout_seconds is not None
            else self.config.job_timeout_seconds
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.cancel(job_id)
                raise RemoteGpuInferenceTimeout(
                    "Remote inference exceeded its caller deadline; cancellation was requested"
                )
            try:
                job = self._request(
                    "GET",
                    f"/api/v1/jobs/{UUID(job_id)}",
                    read_timeout_seconds=remaining,
                )
            except RemoteGpuConnectionError as error:
                if isinstance(error.__cause__, httpx.ReadTimeout) and time.monotonic() >= deadline:
                    self.cancel(job_id)
                    raise RemoteGpuInferenceTimeout(
                        "Remote inference exceeded its caller deadline; cancellation was requested"
                    ) from error
                raise
            status = job.get("status")
            if status in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}:
                if status != "COMPLETED":
                    raise RemoteGpuJobError(
                        f"Remote inference ended with status {status} "
                        f"(error_code={job.get('error_code')})"
                    )
                if job.get("mock") is not True:
                    raise RemoteGpuNodeError("Completed job was not marked as mock")
                response = job.get("response")
                if not isinstance(response, str) or not response.startswith("MOCK ONLY:"):
                    raise RemoteGpuNodeError(
                        "Completed job did not contain an explicit mock result"
                    )
                return job
            if status not in {"QUEUED", "RUNNING"}:
                raise RemoteGpuNodeError("Remote GPU node returned an unknown job state")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.cancel(job_id)
                raise RemoteGpuInferenceTimeout(
                    "Remote inference exceeded its caller deadline; cancellation was requested"
                )
            time.sleep(min(self.config.poll_interval_seconds, remaining))

    def generate(self, prompt: str, *, timeout_seconds: int | None = None) -> str:
        request_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self.config.job_timeout_seconds
        )
        job_id = self.submit(prompt, timeout_seconds=request_timeout)
        try:
            result = self.wait_for_job(job_id, timeout_seconds=request_timeout + 5)
        except RemoteGpuInferenceTimeout:
            raise
        response = result["response"]
        if not isinstance(response, str):
            raise RemoteGpuNodeError("Completed remote job has no response text")
        return response

    def shutdown(self) -> None:
        self._client.close()

    def __enter__(self) -> "RemoteGpuRuntimeAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
