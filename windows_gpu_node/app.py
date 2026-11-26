import hmac
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from windows_gpu_node import __version__
from windows_gpu_node.config import NodeConfig
from windows_gpu_node.hardware import discover_capabilities
from windows_gpu_node.jobs import JobManager, MockRuntime
from windows_gpu_node.models import InferenceRequest

HardwareProbe = Callable[[], dict[str, Any]]
LOGGER = logging.getLogger("windows_gpu_node")


class _RequestBodyTooLarge(Exception):
    pass


async def _json_error(send: Send, status_code: int, detail: str) -> None:
    body = ('{"detail":"' + detail + '"}').encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, maximum_bytes: int) -> None:
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await _json_error(send, 400, "Invalid Content-Length")
                return
            if declared_length < 0 or declared_length > self.maximum_bytes:
                await _json_error(send, 413, "Request body too large")
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.maximum_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if not response_started:
                await _json_error(send, 413, "Request body too large")


class BearerAndSourceMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        config: NodeConfig,
        token: str,
    ) -> None:
        self.app = app
        self.config = config
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        source_address = client[0] if client else None
        if not self.config.permits_source(source_address):
            await _json_error(send, 403, "Source address is not allowed")
            return

        headers = dict(scope.get("headers", []))
        raw_authorization = headers.get(b"authorization", b"")
        try:
            authorization = raw_authorization.decode("ascii")
        except UnicodeDecodeError:
            authorization = ""
        scheme, separator, supplied_token = authorization.partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not hmac.compare_digest(supplied_token, self.token)
        ):
            await _json_error(send, 401, "Bearer token required")
            return

        await self.app(scope, receive, send)


def create_app(
    config: NodeConfig,
    *,
    auth_token: str,
    hardware_probe: HardwareProbe = discover_capabilities,
    runtime: MockRuntime | None = None,
) -> FastAPI:
    if len(auth_token) < 32:
        raise ValueError("Bearer token must contain at least 32 characters")

    mock_runtime = runtime or MockRuntime(delay_seconds=config.mock_delay_seconds)
    jobs = JobManager(config, mock_runtime)
    started_at: datetime | None = None
    capabilities: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        nonlocal started_at
        started_at = datetime.now(UTC)
        capabilities.update(hardware_probe())
        await jobs.start()
        LOGGER.info("Agent started node_id=%s mock_only=true", config.node_id)
        try:
            yield
        finally:
            LOGGER.info("Agent stopping node_id=%s", config.node_id)
            await jobs.stop()

    app = FastAPI(
        title="Windows GPU Node",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.jobs = jobs
    app.state.config = config
    app.add_middleware(
        RequestSizeLimitMiddleware,
        maximum_bytes=config.max_request_bytes,
    )
    app.add_middleware(
        BearerAndSourceMiddleware,
        config=config,
        token=auth_token,
    )

    @app.get("/api/v1/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "node_id": config.node_id,
            "agent_version": __version__,
            "state": jobs.state,
            "mock_only": True,
            "started_at": started_at.isoformat() if started_at else None,
        }

    @app.get("/api/v1/capabilities")
    async def get_capabilities() -> dict[str, object]:
        return {
            "node_id": config.node_id,
            "agent_version": __version__,
            "mock_only": True,
            "inference_backend": "mock",
            "max_concurrent_jobs": 1,
            "hardware": capabilities,
        }

    @app.get("/api/v1/metrics")
    async def get_metrics() -> dict[str, object]:
        current_hardware = hardware_probe()
        nvidia = current_hardware.get("nvidia", {})
        devices = nvidia.get("devices", []) if isinstance(nvidia, dict) else []
        gpu_metrics_available = bool(nvidia.get("nvidia_smi_available") and devices)
        active_jobs = sum(
            1 for job in jobs.jobs.values() if job.status.value == "RUNNING"
        )
        return {
            "node_id": config.node_id,
            "state": jobs.state,
            "uptime_seconds": (
                max(0, int((datetime.now(UTC) - started_at).total_seconds()))
                if started_at
                else 0
            ),
            "queue_depth": jobs.queue_depth,
            "active_jobs": active_jobs,
            "hardware": current_hardware,
            "gpu_metrics_available": gpu_metrics_available,
        }

    @app.get("/api/v1/models")
    async def list_models() -> dict[str, object]:
        return {
            "models": [
                {
                    "id": config.mock_model_id,
                    "backend": "mock",
                    "mock": True,
                    "loaded": False,
                }
            ]
        }

    @app.post("/api/v1/inference", status_code=202)
    async def submit_inference(
        inference_request: InferenceRequest,
    ) -> dict[str, object]:
        if jobs.draining:
            raise HTTPException(status_code=503, detail="Node is draining")
        if inference_request.model != config.mock_model_id:
            raise HTTPException(
                status_code=422,
                detail="Only the configured mock model is available in this release",
            )
        if len(inference_request.prompt) > config.max_prompt_chars:
            raise HTTPException(status_code=413, detail="Prompt is too large")
        if (
            inference_request.system_prompt is not None
            and len(inference_request.system_prompt) > config.max_system_prompt_chars
        ):
            raise HTTPException(status_code=413, detail="System prompt is too large")

        try:
            job, _ = jobs.submit(inference_request)
        except OverflowError as error:
            raise HTTPException(status_code=429, detail="Inference queue is full") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return job.to_dict()

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, object]:
        try:
            parsed_id = UUID(job_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        job = jobs.get(parsed_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job.to_dict()

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, object]:
        try:
            parsed_id = UUID(job_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Job not found") from error
        job = jobs.cancel(parsed_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job.to_dict()

    @app.post("/api/v1/node/drain")
    async def drain() -> dict[str, str]:
        jobs.drain()
        return {"state": jobs.state}

    @app.post("/api/v1/node/resume")
    async def resume() -> dict[str, str]:
        jobs.resume()
        return {"state": jobs.state}

    return app
