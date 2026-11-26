import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drx.cli.main import app
from drx.deployment.readiness import (
    CheckStatus,
    ModelMemoryProfile,
    create_readiness_report,
    estimate_model_vram,
)


class FixtureCommandRunner:
    def __init__(self, *, public_listener: bool = False) -> None:
        self.public_listener = public_listener

    def __call__(
        self, command: list[str], _timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if command[0] == "nvidia-smi":
            if len(command) > 1:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "NVIDIA GeForce GTX, 6144 MiB, 5120 MiB, 555.42\n",
                    "",
                )
            return subprocess.CompletedProcess(
                command, 0, "CUDA Version: 12.5\n", ""
            )
        if command[0] == "nvcc":
            return subprocess.CompletedProcess(
                command, 0, "Cuda compilation tools, release 12.4, V12.4.0\n", ""
            )
        if command[0] == sys.executable:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "installed": True,
                        "version": "2.5.0",
                        "compiled_cuda": "12.4",
                        "cuda_available": True,
                        "devices": [
                            {
                                "name": "NVIDIA GeForce GTX",
                                "total_memory_bytes": 6 * 1024**3,
                                "compute_capability": "6.1",
                            }
                        ],
                    }
                ),
                "",
            )
        if command[:2] == ["systemctl", "--version"]:
            return subprocess.CompletedProcess(command, 0, "systemd 252\n", "")
        if command[:2] == ["systemctl", "is-system-running"]:
            return subprocess.CompletedProcess(command, 0, "running\n", "")
        if command[:3] == ["ss", "-H", "-lnt"]:
            address = "0.0.0.0:8010" if self.public_listener else "127.0.0.1:8010"
            return subprocess.CompletedProcess(
                command, 0, f"LISTEN 0 128 {address} 0.0.0.0:*\n", ""
            )
        if command[:3] == ["ufw", "status", "verbose"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Status: active\nDefault: deny (incoming), allow (outgoing)\n",
                "",
            )
        if command[:5] == ["git", "config", "--global", "--get-all", "credential.helper"]:
            return subprocess.CompletedProcess(command, 0, "manager-core\n", "")
        raise AssertionError(f"unexpected readiness probe command: {command}")


def _profile(parameters_billions: float = 1) -> ModelMemoryProfile:
    return ModelMemoryProfile(
        profile_id="test-profile-v1",
        parameters_billions=parameters_billions,
        weight_bits=8,
        weight_overhead_ratio=0,
        context_tokens=10,
        layers=2,
        kv_heads=2,
        head_dimension=4,
        kv_bits=8,
        safety_margin_ratio=0,
    )


def test_model_memory_estimate_includes_weights_and_kv_cache() -> None:
    estimate = estimate_model_vram(_profile())

    assert estimate["weights_bytes"] == 1_000_000_000
    assert estimate["kv_cache_bytes"] == 320
    assert estimate["required_bytes"] == 1_000_000_320


def test_model_profile_rejects_non_finite_size() -> None:
    with pytest.raises(ValueError, match="finite"):
        ModelMemoryProfile(
            profile_id="infinite-model",
            parameters_billions=float("inf"),
            context_tokens=1,
            layers=1,
            kv_heads=1,
            head_dimension=1,
        )


def test_readiness_cli_writes_new_evidence_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = {"overall_status": "needs_review", "evidence_sha256": "a" * 64}
    monkeypatch.setattr("drx.cli.main.create_readiness_report", lambda **_kwargs: report)
    runner = CliRunner()

    standard_output = runner.invoke(app, ["node", "readiness", "--node-id", "test-node"])
    output_path = tmp_path / "readiness.json"
    saved_report = runner.invoke(
        app,
        ["node", "readiness", "--output", str(output_path)],
    )

    assert standard_output.exit_code == 0
    assert json.loads(standard_output.stdout) == report
    assert saved_report.exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == report


def test_readiness_cli_refuses_to_overwrite_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "drx.cli.main.create_readiness_report",
        lambda **_kwargs: {"evidence_sha256": "b" * 64},
    )
    output_path = tmp_path / "existing.json"
    output_path.write_text("existing evidence", encoding="utf-8")

    result = CliRunner().invoke(app, ["node", "readiness", "--output", str(output_path)])

    assert result.exit_code != 0
    assert output_path.read_text(encoding="utf-8") == "existing evidence"


@pytest.mark.parametrize(
    ("parameters_billions", "model_status"),
    [(1, CheckStatus.PASS.value), (10, CheckStatus.FAIL.value)],
)
def test_readiness_report_is_hashed_and_marks_network_exposure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parameters_billions: float,
    model_status: str,
) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=debian\nVERSION_ID="12"\n', encoding="utf-8")
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    workspace.chmod(0o700)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("drx.deployment.readiness.platform.system", lambda: "Linux")
    monkeypatch.setattr("drx.deployment.readiness.platform.machine", lambda: "x86_64")

    report = create_readiness_report(
        node_id="test-node",
        workspace_root=workspace,
        control_repository=tmp_path / "control",
        model_profile=_profile(parameters_billions),
        runtime_configured=True,
        os_release_path=os_release,
        home=home,
        command_runner=FixtureCommandRunner(public_listener=True),
    )

    checks = {check["id"]: check for check in report["checks"]}
    assert report["scope"] == "local-host-read-only"
    assert report["overall_status"] == "blocked"
    assert checks["operating_system"]["status"] == CheckStatus.PASS.value
    assert checks["nvidia_driver"]["status"] == CheckStatus.PASS.value
    assert checks["cuda_toolkit"]["details"]["toolkit_version"] == "12.4"
    assert checks["pytorch_cuda_runtime"]["status"] == CheckStatus.PASS.value
    assert checks["model_memory_estimate"]["status"] == model_status
    assert checks["network_listeners"]["status"] == CheckStatus.FAIL.value
    assert checks["host_firewall"]["status"] == CheckStatus.WARN.value
    assert checks["host_firewall"]["details"]["default_outgoing"] == "allow"
    assert checks["runtime_configuration"]["details"]["model_request_sent"] is False
    assert checks["git_credentials"]["details"]["secret_values_read"] is False

    unsigned_report = {key: value for key, value in report.items() if key != "evidence_sha256"}
    expected_digest = hashlib.sha256(
        json.dumps(
            unsigned_report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    assert report["evidence_sha256"] == expected_digest


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode assertions require Unix")
def test_workspace_check_rejects_group_or_other_writable_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    workspace.chmod(0o777)

    report = create_readiness_report(
        workspace_root=workspace,
        control_repository=tmp_path / "control",
        home=tmp_path / "home",
        os_release_path=tmp_path / "missing-os-release",
        command_runner=FixtureCommandRunner(),
    )
    checks = {check["id"]: check for check in report["checks"]}

    assert checks["workspace_permissions"]["status"] == CheckStatus.FAIL.value


@pytest.mark.skipif(os.name != "posix", reason="POSIX key-mode assertions require Unix")
def test_git_key_check_reports_unsafe_mode_without_reading_key_contents(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    ssh_directory = home / ".ssh"
    ssh_directory.mkdir(parents=True)
    ssh_directory.chmod(0o700)
    private_key = ssh_directory / "id_ed25519"
    private_key.write_text("supersecret-marker", encoding="utf-8")
    private_key.chmod(0o644)

    report = create_readiness_report(
        home=home,
        os_release_path=tmp_path / "missing-os-release",
        command_runner=FixtureCommandRunner(),
    )
    checks = {check["id"]: check for check in report["checks"]}

    assert checks["git_credentials"]["status"] == CheckStatus.FAIL.value
    assert checks["git_credentials"]["details"]["secret_values_read"] is False
    assert "supersecret-marker" not in json.dumps(report)
