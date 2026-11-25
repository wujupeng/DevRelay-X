"""Compatibility exports for the original Agent test-runner import path."""

from drx.evidence.test import TestEvidence
from drx.execution.test_runner import TestRunner

TestResult = TestEvidence

__all__ = ["TestEvidence", "TestResult", "TestRunner"]
