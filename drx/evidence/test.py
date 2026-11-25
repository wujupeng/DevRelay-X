from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class TestEvidence:
    __test__ = False

    framework: str
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    tests_run: int
    passed: bool
    timed_out: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
