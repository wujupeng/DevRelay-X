import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from drx.runtime.remote_gpu import (
    RemoteGpuConnectionError,
    RemoteGpuInferenceTimeout,
    RemoteGpuJobError,
    RemoteGpuNodeError,
    RemoteGpuRuntimeAdapter,
    load_remote_gpu_config,
)


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
            cwd=Path(__file__).resolve().parents[1],
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return result.stdout.strip()


def _auth_probe(endpoint: str, ca_bundle: Path, token: str | None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(
        verify=str(ca_bundle),
        timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5),
        trust_env=False,
    ) as client:
        response = client.get(f"{endpoint}/api/v1/health", headers=headers)
    return response.status_code


def _check_untrusted_ca(endpoint: str, ca_bundle: Path) -> bool:
    try:
        with httpx.Client(
            verify=str(ca_bundle),
            timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5),
            trust_env=False,
        ) as client:
            client.get(f"{endpoint}/api/v1/health")
    except httpx.ConnectError as error:
        message = str(error).casefold()
        return any(
            indicator in message
            for indicator in (
                "certificate_verify_failed",
                "certificate verify failed",
                "unable to get local issuer certificate",
            )
        )
    except httpx.HTTPError:
        return False
    return False


def _check_ollama_closed(host: str, port: int = 11434) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return False
    except OSError:
        return True


def run_acceptance(
    config_path: Path,
    output_path: Path,
    *,
    untrusted_ca_bundle: Path | None,
    expect_offline: bool,
    expected_agent_commit_sha: str | None,
) -> int:
    config = load_remote_gpu_config(config_path)
    token = os.environ.get(config.token_env_var, "")
    if len(token) < 32:
        raise ValueError(f"{config.token_env_var} must be set with at least 32 characters")

    report: dict[str, object] = {
        "test_machine": socket.gethostname(),
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "commit_sha": _git_sha(),
        "endpoint": config.endpoint,
        "mode": "expect_offline" if expect_offline else "online",
        "results": {},
    }
    results = report["results"]
    assert isinstance(results, dict)

    if expect_offline:
        adapter = RemoteGpuRuntimeAdapter(config)
        try:
            try:
                adapter.connect()
            except RemoteGpuConnectionError as error:
                results["node_offline"] = {
                    "passed": True,
                    "error_type": type(error).__name__,
                    "detail": str(error),
                }
            else:
                results["node_offline"] = {"passed": False}
        finally:
            adapter.shutdown()
        return _write_report(report, output_path)

    if expected_agent_commit_sha is None or not re.fullmatch(
        r"[0-9a-f]{40}", expected_agent_commit_sha
    ):
        raise ValueError(
            "Online acceptance requires --expected-agent-commit-sha "
            "with the reviewed 40-character SHA"
        )

    no_token_status = _auth_probe(config.endpoint, config.ca_bundle_file, None)
    wrong_token_status = _auth_probe(
        config.endpoint,
        config.ca_bundle_file,
        "invalid-token-" + ("x" * 40),
    )
    results["authentication"] = {
        "missing_token_status": no_token_status,
        "wrong_token_status": wrong_token_status,
        "passed": no_token_status == 401 and wrong_token_status == 401,
    }
    if untrusted_ca_bundle is not None:
        tls_rejected = _check_untrusted_ca(config.endpoint, untrusted_ca_bundle)
        results["untrusted_ca_rejected"] = {"passed": tls_rejected}
    else:
        results["untrusted_ca_rejected"] = {
            "passed": False,
            "detail": "Not run; provide an unrelated, valid CA bundle.",
        }

    adapter = RemoteGpuRuntimeAdapter(config)
    try:
        health = adapter.connect()
        reported_commit = health.get("agent_commit_sha")
        if reported_commit != expected_agent_commit_sha:
            raise RemoteGpuNodeError(
                "Windows Agent commit SHA did not match the reviewed SHA"
            )
        capabilities = adapter.get_capabilities()
        metrics = adapter.get_metrics()
        models = adapter.list_models()
        hardware = capabilities.get("hardware", {})
        nvidia = hardware.get("nvidia", {}) if isinstance(hardware, dict) else {}
        devices = nvidia.get("devices", []) if isinstance(nvidia, dict) else []
        gpu = next(
            (
                device
                for device in devices
                if isinstance(device, dict) and "RTX A400" in str(device.get("name", ""))
            ),
            None,
        )
        gpu_fields_valid = (
            gpu is not None
            and isinstance(gpu.get("memory_total_mib"), int)
            and isinstance(gpu.get("memory_free_mib"), int)
            and isinstance(gpu.get("driver_version"), str)
            and isinstance(nvidia.get("driver_cuda_compatibility"), str)
        )
        toolkit = hardware.get("cuda_toolkit")
        ollama = hardware.get("ollama")
        results["node"] = {
            "health": health,
            "agent_commit_sha": reported_commit,
            "capabilities": capabilities,
            "metrics": metrics,
            "models": models,
            "gpu_found": gpu is not None,
            "gpu_fields_valid": gpu_fields_valid,
            "gpu": gpu,
            "driver_cuda_compatibility": nvidia.get("driver_cuda_compatibility"),
            "cuda_toolkit": toolkit,
            "ollama": ollama,
            "framework_cuda_inference_tested": False,
            "gpu_inference_verified": False,
            "passed": (
                health.get("agent_commit_sha") == expected_agent_commit_sha
                and gpu_fields_valid
                and nvidia.get("nvidia_smi_available") is True
                and isinstance(toolkit, dict)
                and isinstance(toolkit.get("available"), bool)
                and isinstance(ollama, dict)
                and isinstance(ollama.get("installed"), bool)
            ),
        }
        results["ollama_port_11434_closed"] = {
            "passed": _check_ollama_closed("192.168.2.85")
        }

        mock_jobs: list[dict[str, object]] = []
        for index in range(20):
            request_id = uuid4()
            prompt = f"Cross-host mock acceptance probe {index + 1:02d}"
            started = time.monotonic()
            job_id = adapter.submit(prompt, request_id=request_id)
            job = adapter.wait_for_job(job_id)
            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
            mock_jobs.append(
                {
                    "request": {
                        "request_id": str(request_id),
                        "model": config.model_id,
                        "prompt": prompt,
                    },
                    "response": {
                        "job_id": job_id,
                        "status": job["status"],
                        "mock": job["mock"],
                        "response": job["response"],
                    },
                    "duration_ms": elapsed_ms,
                }
            )
        for item in mock_jobs:
            response = item["response"]
            item["passed"] = (
                isinstance(response, dict)
                and response.get("status") == "COMPLETED"
                and response.get("mock") is True
                and str(response.get("response", "")).startswith("MOCK ONLY:")
            )
        successes = sum(item.get("passed") is True for item in mock_jobs)
        results["mock_inference_20"] = {
            "successes": successes,
            "total": len(mock_jobs),
            "success_rate_percent": successes * 5,
            "jobs": mock_jobs,
            "passed": successes == 20,
        }

        concurrency_ids = [uuid4(), uuid4()]
        concurrency_job_ids = [
            adapter.submit(
                f"Cross-host queue acceptance probe {index + 1}",
                request_id=request_id,
            )
            for index, request_id in enumerate(concurrency_ids)
        ]
        concurrency_deadline = time.monotonic() + config.job_timeout_seconds + 5
        running_job_samples: list[int] = []
        status_snapshots: list[dict[str, str]] = []
        observed_queued = False
        concurrency_jobs: dict[str, dict[str, object]] = {}
        while len(concurrency_jobs) < len(concurrency_job_ids):
            statuses: dict[str, str] = {}
            for job_id in concurrency_job_ids:
                job = adapter.get_job(job_id)
                status = str(job.get("status", "UNKNOWN"))
                statuses[job_id] = status
                observed_queued = observed_queued or job.get("status") == "QUEUED"
                if job.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}:
                    concurrency_jobs[job_id] = job
            status_snapshots.append(statuses)
            running_job_samples.append(
                sum(status == "RUNNING" for status in statuses.values())
            )
            if len(concurrency_jobs) == len(concurrency_job_ids):
                break
            if time.monotonic() >= concurrency_deadline:
                raise RemoteGpuInferenceTimeout("Concurrent queue probe exceeded its deadline")
            time.sleep(config.poll_interval_seconds)
        concurrency_passed = (
            len(concurrency_jobs) == 2
            and all(job.get("status") == "COMPLETED" and job.get("mock") is True
                    for job in concurrency_jobs.values())
            and observed_queued
            and bool(running_job_samples)
            and max(running_job_samples) <= 1
        )
        results["queue_and_concurrency"] = {
            "request_ids": [str(request_id) for request_id in concurrency_ids],
            "job_ids": concurrency_job_ids,
            "observed_queued_state": observed_queued,
            "running_job_samples": running_job_samples,
            "status_snapshots": status_snapshots,
            "jobs": concurrency_jobs,
            "passed": concurrency_passed,
        }

        cancellation_request_id = uuid4()
        cancellation_job_id = adapter.submit(
            "Cross-host cancellation acceptance probe",
            timeout_seconds=min(10, config.job_timeout_seconds),
            request_id=cancellation_request_id,
        )
        cancelled = adapter.cancel(cancellation_job_id)
        results["cancellation"] = {
            "request_id": str(cancellation_request_id),
            "job_id": cancellation_job_id,
            "status": cancelled.get("status"),
            "passed": cancelled.get("status") == "CANCELLED",
        }

        timeout_request_id = uuid4()
        timeout_job_id = adapter.submit(
            "Cross-host timeout acceptance probe",
            timeout_seconds=1,
            request_id=timeout_request_id,
        )
        try:
            adapter.wait_for_job(timeout_job_id, timeout_seconds=5)
        except RemoteGpuJobError:
            pass
        except RemoteGpuInferenceTimeout:
            pass
        timeout_result = adapter.get_job(timeout_job_id)
        timed_out = timeout_result.get("status") == "TIMEOUT"
        results["remote_inference_timeout"] = {
            "request_id": str(timeout_request_id),
            "job_id": timeout_job_id,
            "request": {
                "model": config.model_id,
                "prompt": "Cross-host timeout acceptance probe",
                "timeout_seconds": 1,
            },
            "response": timeout_result,
            "passed": timed_out,
        }
    finally:
        adapter.shutdown()

    return _write_report(report, output_path)


def _write_report(report: dict[str, object], output_path: Path) -> int:
    results = report.get("results", {})
    passed = isinstance(results, dict) and all(
        not isinstance(result, dict) or result.get("passed") is not False
        for result in results.values()
    )
    report["overall_pass"] = passed
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Evidence report written to {output_path}")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Debian-to-WindowsGpuNode acceptance probe")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--untrusted-ca-bundle", type=Path)
    parser.add_argument("--expected-agent-commit-sha")
    parser.add_argument(
        "--expect-offline",
        action="store_true",
        help="Run while the Windows Agent is intentionally stopped.",
    )
    arguments = parser.parse_args()
    try:
        return run_acceptance(
            arguments.config,
            arguments.output,
            untrusted_ca_bundle=arguments.untrusted_ca_bundle,
            expect_offline=arguments.expect_offline,
            expected_agent_commit_sha=arguments.expected_agent_commit_sha,
        )
    except (OSError, ValueError, RemoteGpuNodeError) as error:
        print(f"Acceptance probe failed: {type(error).__name__}: {error}", file=sys.stderr)
        failure_report: dict[str, object] = {
            "test_machine": socket.gethostname(),
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "commit_sha": _git_sha(),
            "mode": "expect_offline" if arguments.expect_offline else "online",
            "results": {},
            "fatal_error": {
                "type": type(error).__name__,
                "detail": str(error),
            },
            "overall_pass": False,
        }
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(failure_report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Failure evidence report written to {arguments.output}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
