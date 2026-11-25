from unittest.mock import patch

from drx.hardware import _gpu_discovery


def test_gpu_discovery_reports_unavailable_without_blocking() -> None:
    with patch("drx.hardware.subprocess.run", side_effect=FileNotFoundError):
        report = _gpu_discovery()
    assert "available" in report
    assert "memory" in report
    assert "driver" in report
    assert "cuda_version" in report
    assert report["available"] is False
