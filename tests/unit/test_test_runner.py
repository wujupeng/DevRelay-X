from pathlib import Path

import pytest

from drx.agent.config import TestConfig
from drx.execution.test_runner import TestRunner


def test_runner_requires_real_test_execution(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_value.py").write_text(
        "def test_value():\n    assert 2 + 2 == 4\n", encoding="utf-8"
    )
    result = TestRunner().run(tmp_path)
    assert result.framework == "pytest"
    assert result.exit_code == 0
    assert result.tests_run == 1
    assert result.passed


def test_runner_blocks_when_no_test_framework_exists(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="detected"):
        TestRunner(TestConfig(framework="auto")).run(tmp_path)
