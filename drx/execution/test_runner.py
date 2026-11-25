import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from drx.agent.config import TestConfig
from drx.evidence.test import TestEvidence

TestResult = TestEvidence


class TestRunner:
    __test__ = False

    def __init__(self, config: TestConfig | None = None) -> None:
        self.config = config or TestConfig()

    def detect_framework(self, workspace: str | Path) -> str | None:
        root = Path(workspace)
        pytest_markers = (
            root / "pytest.ini",
            root / "pyproject.toml",
            root / "tests",
        )
        if any(path.exists() for path in pytest_markers) and (
            (root / "tests").exists()
            or (root / "pytest.ini").exists()
            or "[tool.pytest" in (root / "pyproject.toml").read_text(
                encoding="utf-8", errors="ignore"
            )
        ):
            return "pytest"
        if list(root.rglob("test*.py")) or list(root.rglob("*_test.py")):
            return "unittest"
        return None

    def run(self, workspace: str | Path) -> TestEvidence:
        root = Path(workspace).resolve()
        framework = self.config.framework
        detected = self.detect_framework(root) if framework == "auto" else framework
        if detected is None:
            raise FileNotFoundError("no supported Python test framework was detected")
        if detected == "pytest" and importlib.util.find_spec("pytest") is None:
            raise FileNotFoundError("pytest is not installed in the selected Python environment")
        command = (
            (sys.executable, "-m", "pytest", "-q")
            if detected == "pytest"
            else (sys.executable, "-m", "unittest", "discover")
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_TERMINAL_PROMPT"] = "0"
        start = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
            duration = time.monotonic() - start
            output = result.stdout + result.stderr
            tests_run = self._test_count(detected, output)
            tests_passed = (
                self._pytest_passed_count(output)
                if detected == "pytest"
                else tests_run
            )
            return TestEvidence(
                detected,
                command,
                result.returncode,
                result.stdout,
                result.stderr,
                duration,
                tests_run,
                result.returncode == 0 and tests_run > 0 and tests_passed > 0,
            )
        except subprocess.TimeoutExpired as error:
            duration = time.monotonic() - start
            stdout = self._decode_output(error.stdout)
            stderr = self._decode_output(error.stderr)
            return TestEvidence(
                detected,
                command,
                None,
                stdout,
                stderr,
                duration,
                self._test_count(detected, stdout + stderr),
                False,
                True,
            )

    @staticmethod
    def _test_count(framework: str, output: str) -> int:
        if framework == "pytest":
            return sum(
                int(match.group(1))
                for match in re.finditer(
                    r"(\d+) (?:passed|failed|skipped|xfailed|xpassed)", output
                )
            )
        match = re.search(r"Ran (\d+) tests?", output)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _pytest_passed_count(output: str) -> int:
        return sum(
            int(match.group(1))
            for match in re.finditer(r"(\d+) passed", output)
        )

    @staticmethod
    def _decode_output(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return value or ""
