import csv
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

CommandRunner = Callable[[list[str], float], subprocess.CompletedProcess[str]]
ExecutableLookup = Callable[[str], str | None]


def _run_command(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _command_result(
    command: list[str],
    *,
    runner: CommandRunner,
    timeout: float = 3,
) -> subprocess.CompletedProcess[str] | None:
    try:
        result = runner(command, timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return result if result.returncode == 0 else None


def _number_or_none(value: str, *, integer: bool = True) -> int | float | None:
    stripped = value.strip()
    if not stripped or stripped.upper() in {"N/A", "[N/A]"}:
        return None
    try:
        return int(stripped) if integer else float(stripped)
    except ValueError:
        return None


def _nvidia_info(
    runner: CommandRunner,
    executable_lookup: ExecutableLookup,
) -> dict[str, Any]:
    executable = (
        executable_lookup("nvidia-smi.exe")
        or executable_lookup("nvidia-smi")
    )
    if executable is None:
        return {
            "nvidia_smi_available": False,
            "driver_cuda_compatibility": None,
            "devices": [],
            "error": "nvidia-smi not found",
        }

    query = _command_result(
        [
            executable,
            "--query-gpu=index,name,memory.total,memory.free,driver_version,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        runner=runner,
    )
    if query is None:
        return {
            "nvidia_smi_available": True,
            "driver_cuda_compatibility": None,
            "devices": [],
            "error": "nvidia-smi query failed or timed out",
        }

    devices: list[dict[str, Any]] = []
    for row in csv.reader(query.stdout.splitlines()):
        if len(row) != 7:
            continue
        devices.append(
            {
                "index": _number_or_none(row[0]),
                "name": row[1].strip() or None,
                "memory_total_mib": _number_or_none(row[2]),
                "memory_free_mib": _number_or_none(row[3]),
                "driver_version": row[4].strip() or None,
                "utilization_percent": _number_or_none(row[5]),
                "temperature_c": _number_or_none(row[6]),
            }
        )

    details = _command_result([executable], runner=runner)
    cuda_compatibility: str | None = None
    if details is not None:
        match = re.search(r"CUDA Version:\s*([0-9.]+)", details.stdout)
        if match:
            cuda_compatibility = match.group(1)

    return {
        "nvidia_smi_available": True,
        "driver_cuda_compatibility": cuda_compatibility,
        "devices": devices,
        "error": None if devices else "nvidia-smi returned no GPU devices",
    }


def _cuda_toolkit(
    runner: CommandRunner,
    executable_lookup: ExecutableLookup,
) -> dict[str, Any]:
    executable = executable_lookup("nvcc.exe") or executable_lookup("nvcc")
    if executable is None:
        return {"available": False, "version": None}
    result = _command_result([executable, "--version"], runner=runner)
    if result is None:
        return {"available": True, "version": None, "error": "nvcc query failed or timed out"}
    match = re.search(r"release\s+([0-9.]+)", result.stdout)
    return {
        "available": True,
        "version": match.group(1) if match else None,
        "error": None if match else "nvcc version was not reported",
    }


def _ollama_status(executable_lookup: ExecutableLookup) -> dict[str, Any]:
    executable = executable_lookup("ollama.exe") or executable_lookup("ollama")
    return {
        "installed": executable is not None,
        "executable_available": executable is not None,
        "models": None,
    }


def _cpu_name() -> str | None:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if isinstance(value, str):
                return value.strip()
        except (OSError, ImportError):
            pass
    return platform.processor() or None


def _physical_memory_bytes() -> int | None:
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
    if hasattr(os, "sysconf"):
        try:
            return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError):
            return None
    return None


def _physical_core_count() -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        get_info = ctypes.windll.kernel32.GetLogicalProcessorInformationEx
        byte_count = ctypes.c_ulong(0)
        get_info(0, None, ctypes.byref(byte_count))
        if byte_count.value == 0:
            return None
        buffer = ctypes.create_string_buffer(byte_count.value)
        if not get_info(0, buffer, ctypes.byref(byte_count)):
            return None

        offset = 0
        cores = 0
        while offset + 8 <= byte_count.value:
            relationship = ctypes.c_ulong.from_buffer(buffer, offset).value
            record_size = ctypes.c_ulong.from_buffer(buffer, offset + 4).value
            if record_size < 8 or offset + record_size > byte_count.value:
                return None
            if relationship == 0:
                cores += 1
            offset += record_size
        return cores or None
    except (AttributeError, OSError, ValueError):
        return None


def discover_capabilities(
    *,
    command_runner: CommandRunner | None = None,
    executable_lookup: ExecutableLookup | None = None,
) -> dict[str, Any]:
    runner = command_runner or _run_command
    lookup = executable_lookup or shutil.which
    return {
        "os": {
            "name": platform.system(),
            "version": platform.version(),
            "release": platform.release(),
            "architecture": platform.machine(),
        },
        "cpu": {
            "model": _cpu_name(),
            "physical_cores": _physical_core_count(),
            "logical_processors": os.cpu_count(),
        },
        "system_memory_bytes": _physical_memory_bytes(),
        "nvidia": _nvidia_info(runner, lookup),
        "cuda_toolkit": _cuda_toolkit(runner, lookup),
        "ollama": _ollama_status(lookup),
    }
