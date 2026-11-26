import subprocess
from unittest.mock import patch

from drx.hardware import _gpu_discovery


def test_gpu_discovery_reports_unavailable_without_blocking() -> None:
    with patch("drx.hardware.subprocess.run", side_effect=FileNotFoundError):
        report = _gpu_discovery()
    assert "available" in report
    assert "memory" in report
    assert "memory_free" in report
    assert "driver" in report
    assert "cuda_version" in report
    assert report["available"] is False


def test_gpu_discovery_reports_total_and_free_vram() -> None:
    def command_runner(command: list[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        output = (
            "NVIDIA GeForce GTX, 6144 MiB, 5120 MiB, 555.42\n"
            if len(command) > 1
            else "CUDA Version: 12.5\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    report = _gpu_discovery(command_runner)

    assert report["available"] is True
    assert report["devices"][0]["memory_bytes"] == 6 * 1024**3
    assert report["devices"][0]["memory_free_bytes"] == 5 * 1024**3
    assert report["devices"][0]["driver"] == "555.42"
