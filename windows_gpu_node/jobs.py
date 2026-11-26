import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from windows_gpu_node.config import NodeConfig
from windows_gpu_node.models import InferenceRequest


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


@dataclass(slots=True)
class Job:
    job_id: UUID
    request: InferenceRequest
    request_fingerprint: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    response: str | None = None
    error_code: str | None = None
    duration_ms: int | None = None
    execution: asyncio.Task[str] | None = field(default=None, repr=False)
    started_clock: float | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": str(self.request.request_id),
            "job_id": str(self.job_id),
            "status": self.status.value,
            "model": self.request.model,
            "response": self.response,
            "usage": {"prompt_tokens": None, "completion_tokens": None},
            "metrics": {
                "duration_ms": self.duration_ms,
                "gpu_memory_used_mib": None,
                "resource_metrics_available": False,
            },
            "mock": True,
            "error_code": self.error_code,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class MockRuntime:
    def __init__(self, *, delay_seconds: float = 0) -> None:
        self.delay_seconds = delay_seconds

    async def generate(self, _request: InferenceRequest) -> str:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return "MOCK ONLY: simulated response; no model or GPU inference was performed."


class JobManager:
    def __init__(self, config: NodeConfig, runtime: MockRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self.jobs: dict[UUID, Job] = {}
        self._request_jobs: dict[UUID, UUID] = {}
        self._queue: asyncio.Queue[UUID] = asyncio.Queue(maxsize=config.max_queue_size)
        self._worker: asyncio.Task[None] | None = None
        self._draining = False
        self._active_job: UUID | None = None

    @property
    def draining(self) -> bool:
        return self._draining

    @property
    def state(self) -> str:
        if self._draining:
            return "DRAINING"
        if self._active_job is not None:
            return "BUSY"
        return "ONLINE"

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_queue(), name="mock-job-worker")

    async def stop(self) -> None:
        worker = self._worker
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        for job in self.jobs.values():
            if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                job.status = JobStatus.CANCELLED
                job.error_code = "AGENT_SHUTDOWN"
                job.completed_at = datetime.now(UTC)
                if job.execution is not None:
                    job.execution.cancel()

    def drain(self) -> None:
        self._draining = True

    def resume(self) -> None:
        self._draining = False

    def submit(self, request: InferenceRequest) -> tuple[Job, bool]:
        existing_job_id = self._request_jobs.get(request.request_id)
        fingerprint = request.fingerprint()
        if existing_job_id is not None:
            existing = self.jobs[existing_job_id]
            if existing.request_fingerprint != fingerprint:
                raise ValueError("request_id was already used with a different payload")
            return existing, True

        job = Job(
            job_id=uuid4(),
            request=request,
            request_fingerprint=fingerprint,
        )
        try:
            self._queue.put_nowait(job.job_id)
        except asyncio.QueueFull as error:
            raise OverflowError("inference queue is full") from error

        self.jobs[job.job_id] = job
        self._request_jobs[request.request_id] = job.job_id
        return job, False

    def get(self, job_id: UUID) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: UUID) -> Job | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMEOUT,
        }:
            return job

        job.status = JobStatus.CANCELLED
        job.error_code = "CANCELLED_BY_REQUEST"
        job.completed_at = datetime.now(UTC)
        if job.execution is not None:
            job.execution.cancel()
        return job

    async def _run_queue(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self.jobs.get(job_id)
            try:
                if job is None or job.status != JobStatus.QUEUED:
                    continue
                self._active_job = job_id
                job.status = JobStatus.RUNNING
                job.started_at = datetime.now(UTC)
                job.started_clock = time.monotonic()
                job.execution = asyncio.create_task(
                    self.runtime.generate(job.request),
                    name=f"mock-inference-{job.job_id}",
                )
                timeout = min(
                    job.request.timeout_seconds,
                    self.config.max_job_timeout_seconds,
                )
                try:
                    job.response = await asyncio.wait_for(job.execution, timeout=timeout)
                    if job.status != JobStatus.CANCELLED:
                        job.status = JobStatus.COMPLETED
                except TimeoutError:
                    if job.status != JobStatus.CANCELLED:
                        job.status = JobStatus.TIMEOUT
                        job.error_code = "INFERENCE_TIMEOUT"
                except asyncio.CancelledError:
                    if job.status != JobStatus.CANCELLED:
                        job.status = JobStatus.CANCELLED
                        job.error_code = "CANCELLED"
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                except Exception:
                    job.status = JobStatus.FAILED
                    job.error_code = "MOCK_RUNTIME_ERROR"
                finally:
                    if job.started_clock is not None:
                        job.duration_ms = round(
                            (time.monotonic() - job.started_clock) * 1000
                        )
                    job.completed_at = datetime.now(UTC)
            finally:
                if self._active_job == job_id:
                    self._active_job = None
                self._queue.task_done()
