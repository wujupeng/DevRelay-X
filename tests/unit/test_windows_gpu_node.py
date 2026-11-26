import asyncio
import logging
import subprocess
import time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from windows_gpu_node.app import create_app
from windows_gpu_node.config import NodeConfig
from windows_gpu_node.hardware import discover_capabilities
from windows_gpu_node.jobs import MockRuntime

TOKEN = "test-bearer-token-" + ("x" * 40)


def hardware_report() -> dict[str, object]:
    return {
        "os": {"name": "Windows", "version": "test", "architecture": "AMD64"},
        "cpu": {"model": "Test CPU", "physical_cores": 8, "logical_processors": 16},
        "system_memory_bytes": 16 * 1024**3,
        "nvidia": {
            "nvidia_smi_available": False,
            "driver_cuda_compatibility": None,
            "devices": [],
            "error": "nvidia-smi not found",
        },
        "cuda_toolkit": {"available": False, "version": None},
        "ollama": {"installed": False, "executable_available": False, "models": None},
    }


def build_app(
    *,
    config: NodeConfig | None = None,
    runtime: MockRuntime | None = None,
) -> FastAPI:
    settings = config or NodeConfig()
    return create_app(
        settings,
        auth_token=TOKEN,
        hardware_probe=hardware_report,
        runtime=runtime,
    )


def build_client(application: FastAPI) -> TestClient:
    return TestClient(application, client=("127.0.0.1", 12345))


def headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def inference_body(*, model: str = "mock-only-v1") -> dict[str, object]:
    return {
        "request_id": str(uuid4()),
        "model": model,
        "prompt": "Make a safe test response",
        "system_prompt": "Do not call a real model",
        "max_tokens": 64,
        "temperature": 0.2,
        "timeout_seconds": 10,
    }


def wait_for_status(
    client: TestClient,
    job_id: str,
    expected: set[str],
) -> dict[str, object]:
    for _ in range(100):
        response = client.get(f"/api/v1/jobs/{job_id}", headers=headers())
        assert response.status_code == 200
        job = response.json()
        if job["status"] in expected:
            return job
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not reach one of {expected}")


def test_agent_starts_serves_health_and_stops_cleanly() -> None:
    application = build_app()
    manager = application.state.jobs

    with build_client(application) as client:
        assert manager._worker is not None
        assert not manager._worker.done()
        response = client.get("/api/v1/health", headers=headers())
        assert response.status_code == 200
        assert response.json()["state"] == "ONLINE"
        assert response.json()["mock_only"] is True
        assert response.json()["agent_commit_sha"] == "unknown"

    assert manager._worker is not None
    assert manager._worker.done()


def test_health_reports_installed_agent_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    commit_sha = "a" * 40
    monkeypatch.setenv("DRX_GPU_NODE_COMMIT_SHA", commit_sha)
    with build_client(build_app()) as client:
        response = client.get("/api/v1/health", headers=headers())
    assert response.status_code == 200
    assert response.json()["agent_commit_sha"] == commit_sha


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/capabilities"),
        ("GET", "/api/v1/metrics"),
        ("GET", "/api/v1/models"),
        ("POST", "/api/v1/inference"),
        ("GET", f"/api/v1/jobs/{uuid4()}"),
        ("POST", f"/api/v1/jobs/{uuid4()}/cancel"),
        ("POST", "/api/v1/node/drain"),
        ("POST", "/api/v1/node/resume"),
    ],
)
def test_all_endpoints_require_bearer_authentication(method: str, path: str) -> None:
    with build_client(build_app()) as client:
        response = client.request(method, path)
    assert response.status_code == 401
    assert response.json() == {"detail": "Bearer token required"}


def test_invalid_bearer_token_is_rejected_without_logging_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad_token = "sensitive-wrong-token-which-must-not-appear"
    caplog.set_level(logging.DEBUG)
    with build_client(build_app()) as client:
        response = client.get("/api/v1/health", headers=headers(bad_token))
    assert response.status_code == 401
    assert bad_token not in caplog.text


def test_source_outside_allowlist_is_forbidden() -> None:
    application = build_app()
    with TestClient(application, client=("192.168.2.3", 12345)) as client:
        response = client.get("/api/v1/health", headers=headers())
    assert response.status_code == 403


def test_authenticated_source_is_rate_limited() -> None:
    config = NodeConfig(rate_limit_requests=1, rate_limit_window_seconds=60)
    with build_client(build_app(config=config)) as client:
        first = client.get("/api/v1/health", headers=headers())
        limited = client.get("/api/v1/health", headers=headers())
    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Request rate limit exceeded"}


def test_capabilities_report_unavailable_gpu_ollama_and_toolkit_as_such() -> None:
    with build_client(build_app()) as client:
        response = client.get("/api/v1/capabilities", headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["hardware"]["nvidia"]["nvidia_smi_available"] is False
    assert body["hardware"]["cuda_toolkit"]["available"] is False
    assert body["hardware"]["ollama"]["installed"] is False
    assert body["inference_backend"] == "mock"


def test_mock_inference_completes_and_explicitly_marks_mock_result() -> None:
    with build_client(build_app()) as client:
        submitted = client.post(
            "/api/v1/inference",
            json=inference_body(),
            headers=headers(),
        )
        assert submitted.status_code == 202
        accepted = submitted.json()
        assert accepted["mock"] is True
        completed = wait_for_status(client, accepted["job_id"], {"COMPLETED"})

    assert completed["response"].startswith("MOCK ONLY:")
    assert completed["usage"] == {"prompt_tokens": None, "completion_tokens": None}
    assert completed["metrics"]["resource_metrics_available"] is False
    assert completed["metrics"]["gpu_memory_used_mib"] is None


def test_repeated_request_id_is_idempotent_and_payload_conflicts_are_rejected() -> None:
    body = inference_body()
    with build_client(build_app()) as client:
        first = client.post("/api/v1/inference", json=body, headers=headers())
        repeated = client.post("/api/v1/inference", json=body, headers=headers())
        conflicting_body = {**body, "prompt": "different prompt"}
        conflict = client.post(
            "/api/v1/inference",
            json=conflicting_body,
            headers=headers(),
        )

    assert first.status_code == repeated.status_code == 202
    assert first.json()["job_id"] == repeated.json()["job_id"]
    assert conflict.status_code == 409


def test_mock_inference_timeout_is_reported_as_timeout() -> None:
    runtime = MockRuntime(delay_seconds=2)
    config = NodeConfig(max_job_timeout_seconds=1)
    with build_client(build_app(config=config, runtime=runtime)) as client:
        submitted = client.post(
            "/api/v1/inference",
            json=inference_body(),
            headers=headers(),
        )
        timed_out = wait_for_status(client, submitted.json()["job_id"], {"TIMEOUT"})

    assert timed_out["error_code"] == "INFERENCE_TIMEOUT"
    assert timed_out["response"] is None


def test_metrics_refresh_hardware_snapshot() -> None:
    calls = 0

    def probe() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            **hardware_report(),
            "nvidia": {
                "nvidia_smi_available": True,
                "devices": [{"memory_free_mib": 1000, "utilization_percent": 5}],
            },
        }

    application = create_app(NodeConfig(), auth_token=TOKEN, hardware_probe=probe)
    with build_client(application) as client:
        response = client.get("/api/v1/metrics", headers=headers())

    assert response.status_code == 200
    assert calls == 2
    assert response.json()["gpu_metrics_available"] is True


def test_real_model_name_is_rejected_in_mock_only_stage() -> None:
    with build_client(build_app()) as client:
        response = client.post(
            "/api/v1/inference",
            json=inference_body(model="some-real-model"),
            headers=headers(),
        )
    assert response.status_code == 422
    assert "Only the configured mock model" in response.json()["detail"]


def test_job_can_be_cancelled_while_running() -> None:
    runtime = MockRuntime(delay_seconds=1)
    with build_client(build_app(runtime=runtime)) as client:
        submitted = client.post(
            "/api/v1/inference",
            json=inference_body(),
            headers=headers(),
        )
        job_id = submitted.json()["job_id"]
        wait_for_status(client, job_id, {"RUNNING"})
        cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers())
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"
        assert cancelled.json()["response"] is None


def test_drain_blocks_new_jobs_and_resume_accepts_them() -> None:
    with build_client(build_app()) as client:
        drained = client.post("/api/v1/node/drain", headers=headers())
        assert drained.json() == {"state": "DRAINING"}
        rejected = client.post(
            "/api/v1/inference",
            json=inference_body(),
            headers=headers(),
        )
        assert rejected.status_code == 503

        resumed = client.post("/api/v1/node/resume", headers=headers())
        assert resumed.json() == {"state": "ONLINE"}
        accepted = client.post(
            "/api/v1/inference",
            json=inference_body(),
            headers=headers(),
        )
        assert accepted.status_code == 202


def test_queue_is_serial_and_full_queue_is_rejected() -> None:
    class CountingRuntime(MockRuntime):
        def __init__(self) -> None:
            super().__init__(delay_seconds=0.1)
            self.active = 0
            self.maximum_active = 0

        async def generate(self, _request: object) -> str:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            try:
                await asyncio.sleep(self.delay_seconds)
                return "MOCK ONLY: serialized"
            finally:
                self.active -= 1

    runtime = CountingRuntime()
    config = NodeConfig(max_queue_size=1)
    with build_client(build_app(config=config, runtime=runtime)) as client:
        first = client.post(
            "/api/v1/inference",
            json=inference_body(),
            headers=headers(),
        )
        second = client.post(
            "/api/v1/inference",
            json=inference_body(),
            headers=headers(),
        )
        assert first.status_code == 202
        assert second.status_code == 202
        rejected = client.post(
            "/api/v1/inference",
            json=inference_body(),
            headers=headers(),
        )
        assert rejected.status_code == 429
        wait_for_status(client, second.json()["job_id"], {"COMPLETED"})
    assert runtime.maximum_active == 1


def test_oversized_request_body_is_rejected() -> None:
    config = NodeConfig(max_request_bytes=1024)
    body = b"x" * 1025
    with build_client(build_app(config=config)) as client:
        response = client.post(
            "/api/v1/inference",
            content=body,
            headers={**headers(), "content-type": "application/json"},
        )
    assert response.status_code == 413


def test_unimplemented_shell_and_file_routes_are_not_exposed() -> None:
    application = build_app()
    paths = {route.path for route in application.routes}
    assert paths == {
        "/api/v1/health",
        "/api/v1/capabilities",
        "/api/v1/metrics",
        "/api/v1/models",
        "/api/v1/inference",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/node/drain",
        "/api/v1/node/resume",
    }


def test_gpu_capability_probe_reports_nvidia_and_distinguishes_cuda_toolkit() -> None:
    def lookup(name: str) -> str | None:
        return {
            "nvidia-smi.exe": "nvidia-smi.exe",
            "nvcc.exe": None,
            "nvidia-smi": None,
            "nvcc": None,
            "ollama.exe": None,
            "ollama": None,
        }.get(name)

    def runner(command: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        if len(command) > 1 and "--query-gpu" in command[1]:
            output = "0, NVIDIA RTX A400, 4094, 3723, 596.72, 0, 29\n"
        else:
            output = "NVIDIA-SMI 596.72 Driver Version: 596.72 CUDA Version: 13.2\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    report = discover_capabilities(
        command_runner=runner,
        executable_lookup=lookup,
    )
    gpu = report["nvidia"]["devices"][0]
    assert gpu["name"] == "NVIDIA RTX A400"
    assert gpu["memory_total_mib"] == 4094
    assert report["nvidia"]["driver_cuda_compatibility"] == "13.2"
    assert report["cuda_toolkit"] == {"available": False, "version": None}
    assert report["ollama"]["installed"] is False


def test_short_or_missing_token_fails_closed() -> None:
    with pytest.raises(ValueError, match="at least 32 characters"):
        create_app(NodeConfig(), auth_token="short")


def test_node_config_rejects_lan_binding_and_incomplete_tls() -> None:
    with pytest.raises(ValueError, match="requires enable_lan_access"):
        NodeConfig(bind_host="192.168.2.85")
    with pytest.raises(ValueError, match="specific interface IP"):
        NodeConfig(bind_host="0.0.0.0")
    with pytest.raises(ValueError, match="must not use the Ollama API port"):
        NodeConfig(port=11434)
    with pytest.raises(ValueError, match="must be configured together"):
        NodeConfig(tls_cert_file="certificate.pem")


def test_node_config_lan_exposure_requires_explicit_opt_in_tls_and_remote_allowlist() -> None:
    with pytest.raises(ValueError, match="requires TLS certificate and key files"):
        NodeConfig(
            bind_host="192.168.2.85",
            enable_lan_access=True,
            allowed_sources=["192.168.2.3/32"],
        )
    with pytest.raises(ValueError, match="exactly one authorized source IP"):
        NodeConfig(
            bind_host="192.168.2.85",
            enable_lan_access=True,
            allowed_sources=["192.168.2.3/32", "192.168.2.4/32"],
            tls_cert_file="agent.crt",
            tls_key_file="agent.key",
        )
    config = NodeConfig(
        bind_host="192.168.2.85",
        enable_lan_access=True,
        allowed_sources=["192.168.2.3/32"],
        tls_cert_file="agent.crt",
        tls_key_file="agent.key",
    )
    assert config.bind_host == "192.168.2.85"
    assert config.permits_source("192.168.2.3")
    assert not config.permits_source("192.168.2.4")
