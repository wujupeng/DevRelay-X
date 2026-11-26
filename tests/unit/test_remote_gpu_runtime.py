import json
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from drx.runtime.remote_gpu import (
    RemoteGpuConnectionError,
    RemoteGpuHttpError,
    RemoteGpuInferenceTimeout,
    RemoteGpuNodeConfig,
    RemoteGpuNodeError,
    RemoteGpuRuntimeAdapter,
)

TOKEN = "isolated-test-token-" + ("x" * 40)
JOB_ID = str(uuid4())
_REAL_HTTPX_CLIENT = httpx.Client


def build_config(tmp_path: Path, **overrides: object) -> RemoteGpuNodeConfig:
    ca_bundle = tmp_path / "trusted-ca.pem"
    ca_bundle.write_text("test-only CA placeholder", encoding="utf-8")
    values: dict[str, object] = {
        "endpoint": "https://192.168.2.85:8765",
        "ca_bundle_file": ca_bundle,
        "poll_interval_seconds": 0.001,
        "job_timeout_seconds": 2,
    }
    values.update(overrides)
    return RemoteGpuNodeConfig.model_validate(values)


def build_adapter(
    tmp_path: Path,
    handler,
    **config_overrides: object,
) -> RemoteGpuRuntimeAdapter:
    config = build_config(tmp_path, **config_overrides)

    def mock_client(**kwargs: object) -> httpx.Client:
        assert kwargs["verify"] == str(config.ca_bundle_file)
        assert kwargs["trust_env"] is False
        timeout = kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        return _REAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
            trust_env=False,
        )

    with patch("drx.runtime.remote_gpu.httpx.Client", new=mock_client):
        return RemoteGpuRuntimeAdapter(config, auth_token=TOKEN)


def test_remote_adapter_reads_health_capabilities_metrics_and_models(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path.endswith("/health"):
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "node_id": "Node-002",
                    "mock_only": True,
                },
            )
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={"hardware": {"nvidia": {"devices": [{"name": "NVIDIA RTX A400"}]}}},
            )
        if request.url.path.endswith("/metrics"):
            return httpx.Response(200, json={"state": "ONLINE"})
        return httpx.Response(200, json={"models": [{"id": "mock-only-v1", "mock": True}]})

    adapter = build_adapter(tmp_path, respond)
    assert adapter.connect()["node_id"] == "Node-002"
    assert adapter.health_check()
    capabilities = adapter.get_capabilities()
    devices = capabilities["hardware"]["nvidia"]["devices"]
    assert devices[0]["name"] == "NVIDIA RTX A400"
    assert adapter.get_metrics()["state"] == "ONLINE"
    assert adapter.list_models()[0]["mock"] is True
    adapter.shutdown()


def test_remote_adapter_completes_twenty_explicit_mock_jobs(tmp_path: Path) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "POST" and request.url.path.endswith("/inference"):
            body = json.loads(request.content)
            assert body["model"] == "mock-only-v1"
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
            calls += 1
            return httpx.Response(
                202,
                json={"job_id": JOB_ID, "status": "QUEUED", "mock": True},
            )
        if request.url.path.endswith(f"/jobs/{JOB_ID}"):
            return httpx.Response(
                200,
                json={
                    "job_id": JOB_ID,
                    "status": "COMPLETED",
                    "mock": True,
                    "response": "MOCK ONLY: unit test",
                },
            )
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    adapter = build_adapter(tmp_path, respond)
    results = [adapter.generate(f"isolated prompt {index}") for index in range(20)]
    assert calls == 20
    assert all(result.startswith("MOCK ONLY:") for result in results)
    adapter.shutdown()


def test_remote_adapter_cancels_and_rejects_non_mock_job_results(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"job_id": JOB_ID, "status": "CANCELLED"})
        if request.method == "POST":
            return httpx.Response(
                202,
                json={"job_id": JOB_ID, "status": "QUEUED", "mock": True},
            )
        return httpx.Response(
            200,
            json={"job_id": JOB_ID, "status": "COMPLETED", "mock": False, "response": "forged"},
        )

    adapter = build_adapter(tmp_path, respond)
    assert adapter.cancel(JOB_ID)["status"] == "CANCELLED"
    with pytest.raises(RemoteGpuNodeError, match="not marked as mock"):
        adapter.wait_for_job(JOB_ID)
    with pytest.raises(ValueError):
        adapter.get_job("../../api/v1/health")
    adapter.shutdown()


def test_remote_adapter_requests_cancel_when_caller_deadline_expires(tmp_path: Path) -> None:
    cancel_calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal cancel_calls
        if request.url.path.endswith("/cancel"):
            cancel_calls += 1
            return httpx.Response(200, json={"job_id": JOB_ID, "status": "CANCELLED"})
        if request.method == "POST":
            return httpx.Response(
                202,
                json={"job_id": JOB_ID, "status": "QUEUED", "mock": True},
            )
        return httpx.Response(200, json={"job_id": JOB_ID, "status": "RUNNING", "mock": True})

    adapter = build_adapter(tmp_path, respond)
    job_id = adapter.submit("slow prompt")
    with pytest.raises(RemoteGpuInferenceTimeout):
        adapter.wait_for_job(job_id, timeout_seconds=0.005)
    assert cancel_calls == 1
    adapter.shutdown()


def test_remote_adapter_bounds_job_poll_reads_by_inference_deadline(tmp_path: Path) -> None:
    cancel_calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal cancel_calls
        if request.url.path.endswith("/cancel"):
            cancel_calls += 1
            return httpx.Response(200, json={"job_id": JOB_ID, "status": "CANCELLED"})
        if request.method == "POST":
            return httpx.Response(
                202,
                json={"job_id": JOB_ID, "status": "QUEUED", "mock": True},
            )
        time.sleep(0.02)
        raise httpx.ReadTimeout("slow job poll")

    adapter = build_adapter(tmp_path, respond)
    job_id = adapter.submit("slow prompt")
    with pytest.raises(RemoteGpuInferenceTimeout):
        adapter.wait_for_job(job_id, timeout_seconds=0.01)
    assert cancel_calls == 1
    adapter.shutdown()


def test_remote_adapter_reports_node_offline_and_http_auth_errors(tmp_path: Path) -> None:
    def offline(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    adapter = build_adapter(tmp_path, offline)
    with pytest.raises(RemoteGpuConnectionError):
        adapter.connect()
    assert not adapter.health_check()
    adapter.shutdown()

    def unauthorized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    adapter = build_adapter(tmp_path, unauthorized)
    with pytest.raises(RemoteGpuHttpError) as error:
        adapter.connect()
    assert error.value.status_code == 401
    adapter.shutdown()


def test_remote_config_requires_https_and_a_trusted_ca_bundle(tmp_path: Path) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("test-only", encoding="utf-8")
    with pytest.raises(ValueError, match="base HTTPS URL"):
        RemoteGpuNodeConfig(endpoint="http://192.168.2.85:8765", ca_bundle_file=ca_bundle)
    with pytest.raises(ValueError, match="private IPv4 address"):
        RemoteGpuNodeConfig(endpoint="https://example.com:8765", ca_bundle_file=ca_bundle)
    with pytest.raises(ValueError, match="private IPv4 address"):
        RemoteGpuNodeConfig(endpoint="https://127.0.0.1:8765", ca_bundle_file=ca_bundle)
    with pytest.raises(ValueError, match="restricted to Agent TCP port 8765"):
        RemoteGpuNodeConfig(endpoint="https://192.168.2.85:11434", ca_bundle_file=ca_bundle)
    with pytest.raises(ValueError, match="trusted CA bundle"):
        RemoteGpuNodeConfig(
            endpoint="https://192.168.2.85:8765",
            ca_bundle_file=tmp_path / "missing-ca.pem",
        )
