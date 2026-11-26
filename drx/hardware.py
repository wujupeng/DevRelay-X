import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

CommandRunner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


def _run_command(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _memory_bytes() -> int | None:
    if hasattr(os, "sysconf"):
        try:
            return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError):
            return None
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong),
                    ("available_phys", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_phys)
        except (AttributeError, OSError):
            return None
    return None


def _gpu_discovery(
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    run_command = command_runner or _run_command
    try:
        result = run_command(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader",
            ],
            3,
        )
    except FileNotFoundError:
        return _gpu_unavailable("nvidia-smi is not installed")
    except subprocess.TimeoutExpired:
        return _gpu_unavailable("GPU discovery timed out")
    except OSError:
        return _gpu_unavailable("GPU discovery failed")
    if result.returncode:
        return _gpu_unavailable("nvidia-smi could not query GPU devices")
    devices = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",", maxsplit=3)]
        if len(parts) == 4:
            total_memory = _mib_bytes(parts[1])
            free_memory = _mib_bytes(parts[2])
            devices.append(
                {
                    "name": parts[0],
                    "memory": parts[1],
                    "memory_free": parts[2],
                    "memory_bytes": total_memory,
                    "memory_free_bytes": free_memory,
                    "driver": parts[3],
                }
            )
    cuda_version = None
    cuda_error: str | None
    try:
        details = run_command(["nvidia-smi"], 3)
        if details.returncode:
            cuda_error = "nvidia-smi version query failed"
        else:
            match = re.search(r"CUDA Version:\s*([0-9.]+)", details.stdout)
            cuda_version = match.group(1) if match else None
            cuda_error = None if cuda_version else "CUDA version was not reported"
    except FileNotFoundError:
        cuda_error = "nvidia-smi version information is unavailable"
    except subprocess.TimeoutExpired:
        cuda_error = "CUDA version discovery timed out"
    except OSError:
        cuda_error = "CUDA version discovery failed"
    return {
        "available": bool(devices),
        "devices": devices,
        "memory": [device["memory"] for device in devices],
        "memory_free": [device["memory_free"] for device in devices],
        "driver": [device["driver"] for device in devices],
        "runtime": "nvidia-smi",
        "cuda_version": cuda_version,
        "discovery_error": cuda_error,
    }


def _gpu_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "devices": [],
        "memory": [],
        "memory_free": [],
        "driver": [],
        "runtime": None,
        "cuda_version": None,
        "discovery_error": reason,
    }


def _mib_bytes(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*MiB\s*", value, re.IGNORECASE)
    if match is None:
        return None
    return int(match.group(1)) * 1024 * 1024


def discover_node_hardware(
    path: str | Path = ".",
    *,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    disk = shutil.disk_usage(path)
    return {
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "ram_bytes": _memory_bytes(),
        "gpu": _gpu_discovery(command_runner),
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "python": sys.version,
    }
