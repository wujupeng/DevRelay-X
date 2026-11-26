from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shlex
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..hardware import CommandRunner, discover_node_hardware


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_ASSESSED = "not_assessed"


class ModelMemoryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=128)
    parameters_billions: float = Field(gt=0)
    weight_bits: int = Field(default=4, ge=2, le=32)
    context_tokens: int = Field(gt=0)
    layers: int = Field(gt=0)
    kv_heads: int = Field(gt=0)
    head_dimension: int = Field(gt=0)
    kv_bits: int = Field(default=16, ge=2, le=32)
    runtime_overhead_mib: int = Field(default=0, ge=0)
    safety_margin_ratio: float = Field(default=0.2, ge=0, le=2)
    weight_overhead_ratio: float = Field(default=0.1, ge=0, le=1)

    @field_validator("parameters_billions", "safety_margin_ratio", "weight_overhead_ratio")
    @classmethod
    def require_finite_values(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("model profile values must be finite")
        return value

    @field_validator("profile_id")
    @classmethod
    def require_profile_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model profile ID must not be blank")
        return normalized


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    check_id: str
    status: CheckStatus
    summary: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "status": self.status.value,
            "summary": self.summary,
            "details": dict(self.details),
        }


def estimate_model_vram(profile: ModelMemoryProfile) -> dict[str, int | float]:
    weight_bytes = math.ceil(
        profile.parameters_billions
        * 1_000_000_000
        * profile.weight_bits
        / 8
        * (1 + profile.weight_overhead_ratio)
    )
    kv_cache_bytes = math.ceil(
        2
        * profile.layers
        * profile.kv_heads
        * profile.head_dimension
        * profile.context_tokens
        * profile.kv_bits
        / 8
    )
    runtime_overhead_bytes = profile.runtime_overhead_mib * 1024 * 1024
    raw_required_bytes = weight_bytes + kv_cache_bytes + runtime_overhead_bytes
    required_bytes = math.ceil(raw_required_bytes * (1 + profile.safety_margin_ratio))
    return {
        "weights_bytes": weight_bytes,
        "kv_cache_bytes": kv_cache_bytes,
        "runtime_overhead_bytes": runtime_overhead_bytes,
        "required_bytes": required_bytes,
        "required_gib": round(required_bytes / (1024**3), 2),
    }


def _read_command(
    command: Sequence[str], timeout: float = 5
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _check(
    check_id: str,
    status: CheckStatus,
    summary: str,
    **details: Any,
) -> ReadinessCheck:
    return ReadinessCheck(check_id, status, summary, details)


def _is_posix_runtime() -> bool:
    return os.name == "posix"


def _parse_os_release(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, raw_value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z0-9_]+", key):
            continue
        try:
            parsed = shlex.split(raw_value, posix=True)
        except ValueError:
            continue
        values[key] = parsed[0] if parsed else ""
    return values


def _probe_nvidia_runtime(
    hardware: Mapping[str, Any],
) -> tuple[ReadinessCheck, list[dict[str, Any]]]:
    gpu = hardware["gpu"]
    devices = gpu["devices"]
    if not gpu["available"]:
        return (
            _check(
                "nvidia_driver",
                CheckStatus.FAIL,
                "No NVIDIA GPU and usable driver were detected on this host",
                discovery_error=gpu["discovery_error"],
            ),
            [],
        )

    complete_device_data = all(
        bool(device.get("driver"))
        and isinstance(device.get("memory_bytes"), int)
        and isinstance(device.get("memory_free_bytes"), int)
        for device in devices
    )
    return (
        _check(
            "nvidia_driver",
            CheckStatus.PASS if complete_device_data else CheckStatus.WARN,
            (
                "NVIDIA driver reports device identity, driver, and VRAM"
                if complete_device_data
                else "NVIDIA GPU is detected but driver or VRAM details are incomplete"
            ),
            devices=devices,
            driver_versions=gpu["driver"],
            driver_cuda_compatibility=gpu["cuda_version"],
        ),
        devices,
    )


def _probe_cuda_toolkit(runner: CommandRunner) -> ReadinessCheck:
    try:
        result = runner(["nvcc", "--version"], 5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return _check(
            "cuda_toolkit",
            CheckStatus.NOT_ASSESSED,
            (
                "CUDA Toolkit compiler was not found; some inference runtimes bundle "
                "their own CUDA libraries"
            ),
            error_type=type(error).__name__,
        )
    if result.returncode:
        return _check(
            "cuda_toolkit",
            CheckStatus.WARN,
            "CUDA Toolkit version could not be queried",
            return_code=result.returncode,
        )
    match = re.search(r"\brelease\s+([0-9.]+)", result.stdout, re.IGNORECASE)
    if match is None:
        return _check(
            "cuda_toolkit",
            CheckStatus.WARN,
            "nvcc is present but its Toolkit version could not be parsed",
        )
    return _check(
        "cuda_toolkit",
        CheckStatus.PASS,
        (
            "CUDA Toolkit version detected; this does not prove a specific inference "
            "runtime is compatible"
        ),
        toolkit_version=match.group(1),
    )


_TORCH_PROBE = """
import json
try:
    import torch
except ImportError:
    print(json.dumps({"installed": False}))
else:
    result = {
        "installed": True,
        "version": torch.__version__,
        "compiled_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "devices": [],
    }
    if result["cuda_available"]:
        for index in range(torch.cuda.device_count()):
            device = torch.cuda.get_device_properties(index)
            result["devices"].append({
                "name": device.name,
                "total_memory_bytes": device.total_memory,
                "compute_capability": f"{device.major}.{device.minor}",
            })
    print(json.dumps(result))
"""


def _probe_torch_runtime(runner: CommandRunner) -> ReadinessCheck:
    try:
        result = runner([sys.executable, "-c", _TORCH_PROBE], 20)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return _check(
            "pytorch_cuda_runtime",
            CheckStatus.NOT_ASSESSED,
            "PyTorch CUDA runtime could not be inspected; no model was loaded or run",
            error_type=type(error).__name__,
        )
    if result.returncode:
        return _check(
            "pytorch_cuda_runtime",
            CheckStatus.WARN,
            "PyTorch runtime probe failed; no model was loaded or run",
            return_code=result.returncode,
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return _check(
            "pytorch_cuda_runtime",
            CheckStatus.WARN,
            "PyTorch runtime probe returned invalid diagnostic data",
        )
    if not isinstance(payload, dict):
        return _check(
            "pytorch_cuda_runtime",
            CheckStatus.WARN,
            "PyTorch runtime probe returned an unexpected diagnostic shape",
        )
    if not payload.get("installed"):
        return _check(
            "pytorch_cuda_runtime",
            CheckStatus.NOT_ASSESSED,
            "PyTorch is not installed; compatibility for other inference runtimes is not inferred",
        )
    if payload.get("cuda_available"):
        return _check(
            "pytorch_cuda_runtime",
            CheckStatus.PASS,
            "Installed PyTorch can initialize the detected CUDA runtime; no inference was run",
            version=payload.get("version"),
            compiled_cuda=payload.get("compiled_cuda"),
            devices=payload.get("devices", []),
        )
    return _check(
        "pytorch_cuda_runtime",
        CheckStatus.WARN,
        "Installed PyTorch cannot initialize CUDA; other inference runtimes may differ",
        version=payload.get("version"),
        compiled_cuda=payload.get("compiled_cuda"),
    )


def _probe_model_fit(
    profile: ModelMemoryProfile | None, devices: list[dict[str, Any]]
) -> ReadinessCheck:
    if profile is None:
        return _check(
            "model_memory_estimate",
            CheckStatus.NOT_ASSESSED,
            "No model profile was supplied; model memory fit is intentionally not guessed",
        )
    estimate = estimate_model_vram(profile)
    free_memory: list[int] = []
    for device in devices:
        value = device.get("memory_free_bytes")
        if isinstance(value, int):
            free_memory.append(value)
    if not free_memory:
        return _check(
            "model_memory_estimate",
            CheckStatus.NOT_ASSESSED,
            "A profile estimate was computed, but free GPU memory is unavailable",
            model_profile=profile.model_dump(),
            **estimate,
        )
    maximum_free = max(free_memory)
    fits = estimate["required_bytes"] <= maximum_free
    return _check(
        "model_memory_estimate",
        CheckStatus.PASS if fits else CheckStatus.FAIL,
        (
            "Estimated single-GPU memory fits the current free VRAM"
            if fits
            else (
                "Estimated memory exceeds current free VRAM; runtime offload and "
                "multi-GPU behavior are not assumed"
            )
        ),
        model_profile=profile.model_dump(),
        **estimate,
        maximum_free_vram_bytes=maximum_free,
        estimate_only=True,
    )


def _probe_systemd(runner: CommandRunner) -> ReadinessCheck:
    try:
        version_result = runner(["systemctl", "--version"], 5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return _check(
            "systemd",
            CheckStatus.NOT_ASSESSED,
            "systemd was not detected; service lifecycle is not assessed on this host",
            error_type=type(error).__name__,
        )
    if version_result.returncode:
        return _check(
            "systemd",
            CheckStatus.WARN,
            "systemctl exists but did not report a usable systemd version",
            return_code=version_result.returncode,
        )
    version = (
        version_result.stdout.splitlines()[0]
        if version_result.stdout.splitlines()
        else ""
    )
    try:
        state_result = runner(["systemctl", "is-system-running"], 5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return _check(
            "systemd",
            CheckStatus.NOT_ASSESSED,
            "systemd tools are present but the running service manager could not be queried",
            version=version,
            error_type=type(error).__name__,
        )
    state = state_result.stdout.strip().lower()
    manager_running = state == "running"
    return _check(
        "systemd",
        CheckStatus.PASS if manager_running else CheckStatus.WARN,
        (
            "systemd service manager is running; no service was installed or started"
            if manager_running
            else "systemd tools are present but the service manager is not fully running"
        ),
        version=version,
        manager_state=state or "unknown",
    )


def _is_loopback_listener(address: str) -> bool:
    return (
        address.startswith("127.")
        or address.startswith("[::1]:")
        or address.startswith("::1:")
    )


def _listener_port(address: str) -> str | None:
    if address.startswith("[") and "]:" in address:
        return address.rsplit(":", maxsplit=1)[-1]
    if ":" in address:
        return address.rsplit(":", maxsplit=1)[-1]
    return None


def _probe_listeners(runner: CommandRunner) -> ReadinessCheck:
    try:
        result = runner(["ss", "-H", "-lnt"], 5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return _check(
            "network_listeners",
            CheckStatus.NOT_ASSESSED,
            "TCP listening sockets could not be enumerated",
            error_type=type(error).__name__,
        )
    if result.returncode:
        return _check(
            "network_listeners",
            CheckStatus.NOT_ASSESSED,
            "TCP listening sockets could not be enumerated",
            return_code=result.returncode,
        )
    addresses = [
        parts[-2]
        for line in result.stdout.splitlines()
        if len(parts := line.split()) >= 4
    ]
    exposed = [address for address in addresses if not _is_loopback_listener(address)]
    exposed_ports = sorted(
        {port for address in exposed if (port := _listener_port(address)) is not None}
    )
    return _check(
        "network_listeners",
        CheckStatus.FAIL if exposed else CheckStatus.PASS,
        (
            "One or more TCP listeners are bound beyond loopback and require review"
            if exposed
            else "No non-loopback TCP listeners were detected"
        ),
        listener_count=len(addresses),
        non_loopback_listener_count=len(exposed),
        non_loopback_ports=exposed_ports,
    )


def _probe_firewall(runner: CommandRunner) -> ReadinessCheck:
    try:
        ufw = runner(["ufw", "status", "verbose"], 5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        ufw = None
    if ufw is not None and ufw.returncode == 0:
        ufw_active = re.search(
            r"^Status:\s*active\s*$", ufw.stdout, re.IGNORECASE | re.MULTILINE
        )
        ufw_default_deny = re.search(
            r"^Default:.*\bdeny\s*\(incoming\)",
            ufw.stdout,
            re.IGNORECASE | re.MULTILINE,
        )
        ufw_defaults = re.search(
            r"^Default:\s*(.+)$", ufw.stdout, re.IGNORECASE | re.MULTILINE
        )
        outgoing_match = (
            re.search(
                r"\b(deny|allow|reject)\s*\(outgoing\)",
                ufw_defaults.group(1),
                re.IGNORECASE,
            )
            if ufw_defaults is not None
            else None
        )
        default_outgoing = (
            outgoing_match.group(1).lower()
            if outgoing_match is not None
            else "unknown"
        )
        return _check(
            "host_firewall",
            CheckStatus.WARN
            if ufw_active and ufw_default_deny
            else CheckStatus.WARN
            if ufw_active
            else CheckStatus.FAIL,
            (
                "UFW default-deny inbound policy is confirmed; allow rules still require review"
                if ufw_active and ufw_default_deny
                else "UFW is active but default-deny inbound policy was not confirmed"
                if ufw_active
                else "UFW is not active"
            ),
            firewall="ufw",
            active=bool(ufw_active),
            default_deny=bool(ufw_default_deny),
            default_outgoing=default_outgoing,
            rules_reviewed=False,
        )

    for command, policy_pattern, name in (
        (
            ["nft", "list", "ruleset"],
            r"hook\s+input\b[^\n]*\bpolicy\s+(drop|reject)\b",
            "nftables",
        ),
        (
            ["iptables", "-S", "INPUT"],
            r"^-P\s+INPUT\s+(DROP|REJECT)\s*$",
            "iptables",
        ),
    ):
        try:
            result = runner(command, 5)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode:
            continue
        has_default_deny = bool(
            re.search(policy_pattern, result.stdout, re.IGNORECASE | re.MULTILINE)
        )
        egress_policies: list[str] = []
        if name == "nftables":
            egress_policies = [
                value.lower()
                for value in re.findall(
                    r"hook\s+output\b[^\n]*\bpolicy\s+(accept|drop|reject)\b",
                    result.stdout,
                    re.IGNORECASE,
                )
            ]
        elif name == "iptables":
            try:
                output_result = runner(["iptables", "-S", "OUTPUT"], 5)
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                output_result = None
            if output_result is not None and output_result.returncode == 0:
                output_policy = re.search(
                    r"^-P\s+OUTPUT\s+(ACCEPT|DROP|REJECT)\s*$",
                    output_result.stdout,
                    re.IGNORECASE | re.MULTILINE,
                )
                if output_policy:
                    egress_policies.append(output_policy.group(1).lower())
        return _check(
            "host_firewall",
            CheckStatus.WARN,
            (
                f"{name} default-deny policy detected; allow rules still require review"
                if has_default_deny
                else f"{name} is present but a default-deny input policy was not confirmed"
            ),
            firewall=name,
            default_deny=has_default_deny,
            egress_default_policies=egress_policies,
            rules_reviewed=False,
        )
    return _check(
        "host_firewall",
        CheckStatus.NOT_ASSESSED,
        "No readable host firewall status was available",
    )


def _check_path_owner_and_mode(path: Path, expected_uid: int | None) -> list[str]:
    issues: list[str] = []
    try:
        info = path.lstat()
    except OSError:
        return issues
    if path.is_symlink():
        issues.append(f"{path.name}: symlink")
        return issues
    if expected_uid is not None and info.st_uid != expected_uid:
        issues.append(f"{path.name}: unexpected owner")
    if info.st_mode & 0o077:
        issues.append(f"{path.name}: group/other permissions are set")
    return issues


def _probe_workspace(
    workspace_root: Path, control_repository: Path | None
) -> ReadinessCheck:
    root = workspace_root.expanduser()
    if root.is_symlink():
        return _check(
            "workspace_permissions",
            CheckStatus.FAIL,
            "Workspace root must not be a symlink",
            exists=True,
            symlink=True,
        )
    if not root.exists():
        return _check(
            "workspace_permissions",
            CheckStatus.NOT_ASSESSED,
            "Workspace root does not exist; the probe did not create it",
            exists=False,
        )
    if not root.is_dir():
        return _check(
            "workspace_permissions",
            CheckStatus.FAIL,
            "Workspace root must be a directory",
            exists=True,
            symlink=False,
        )
    resolved = root.resolve()
    if control_repository is not None:
        control = control_repository.expanduser().resolve()
        if resolved == control or control in resolved.parents:
            return _check(
                "workspace_permissions",
                CheckStatus.FAIL,
                "Workspace root is inside the control repository",
                exists=True,
                path=str(resolved),
            )
    if not _is_posix_runtime():
        return _check(
            "workspace_permissions",
            CheckStatus.NOT_ASSESSED,
            "POSIX owner and mode checks are unavailable on this host",
            exists=True,
            writable=os.access(resolved, os.W_OK),
        )
    mode = resolved.stat().st_mode
    writable = os.access(resolved, os.W_OK)
    safe_mode = not (mode & 0o077)
    current_uid = os.geteuid() if hasattr(os, "geteuid") else None
    expected_owner = current_uid is None or resolved.stat().st_uid == current_uid
    return _check(
        "workspace_permissions",
        CheckStatus.PASS if writable and safe_mode and expected_owner else CheckStatus.FAIL,
        (
            "Workspace is owned by the probe user, writable, and private"
            if writable and safe_mode and expected_owner
            else "Workspace must be owned by the service user, writable, and mode 0700 or stricter"
        ),
        exists=True,
        mode=f"{mode & 0o777:04o}",
        writable=writable,
        owner_matches_probe_user=expected_owner,
        group_or_other_accessible=not safe_mode,
    )


def _probe_git_credentials(
    home: Path, runner: CommandRunner, config_path: Path | None
) -> ReadinessCheck:
    if not _is_posix_runtime():
        return _check(
            "git_credentials",
            CheckStatus.NOT_ASSESSED,
            "POSIX key ownership and mode checks are unavailable on this host",
            global_git_config_available_to_agent=False,
            ssh_agent_socket_available=False,
            ssh_agent_socket_available_to_agent=False,
            secret_values_read=False,
        )
    candidates = [
        home / ".ssh",
        home / ".git-credentials",
        home / ".netrc",
        home / ".drx" / "config.yaml",
    ]
    if config_path is not None:
        candidates.append(config_path.expanduser())
    ssh_directory = home / ".ssh"
    try:
        if ssh_directory.is_dir() and not ssh_directory.is_symlink():
            candidates.extend(
                    path
                    for path in ssh_directory.iterdir()
                    if path.is_file()
                    and path.name.startswith("id_")
                    and not path.name.endswith(".pub")
            )
    except OSError:
        pass
    expected_uid = os.geteuid() if hasattr(os, "geteuid") else None
    present = [path for path in candidates if path.exists() or path.is_symlink()]
    issues = [
        issue
        for path in present
        for issue in _check_path_owner_and_mode(path, expected_uid)
    ]
    try:
        helper_result = runner(
            ["git", "config", "--global", "--get-all", "credential.helper"], 5
        )
        helper_configured = helper_result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        helper_configured = False
    if issues:
        return _check(
            "git_credentials",
            CheckStatus.FAIL,
            "Git credential or key files have unsafe ownership, permissions, or symlinks",
            issues=issues,
            credential_files_present=bool(present),
            credential_helper_configured=helper_configured,
            global_git_config_available_to_agent=False,
            ssh_agent_socket_available=bool(os.environ.get("SSH_AUTH_SOCK")),
            ssh_agent_socket_available_to_agent=False,
            secret_values_read=False,
        )
    if not present and not helper_configured:
        return _check(
            "git_credentials",
            CheckStatus.NOT_ASSESSED,
            (
                "No local Git credential source was found; private repository needs "
                "are not established"
            ),
            credential_files_present=False,
            credential_helper_configured=False,
            global_git_config_available_to_agent=False,
            ssh_agent_socket_available=bool(os.environ.get("SSH_AUTH_SOCK")),
            ssh_agent_socket_available_to_agent=False,
            secret_values_read=False,
        )
    return _check(
        "git_credentials",
        CheckStatus.WARN,
        (
            (
                "Credential files have restricted ownership and permissions; "
                "repository access was not tested"
            )
            if present
            else "A Git credential helper is configured but its provider and scope require review"
        ),
        credential_files_present=bool(present),
        credential_helper_configured=helper_configured,
        global_git_config_available_to_agent=False,
        ssh_agent_socket_available=bool(os.environ.get("SSH_AUTH_SOCK")),
        ssh_agent_socket_available_to_agent=False,
        secret_values_read=False,
    )


def create_readiness_report(
    *,
    node_id: str = "unassigned",
    workspace_root: str | Path | None = None,
    control_repository: str | Path | None = None,
    model_profile: ModelMemoryProfile | None = None,
    runtime_configured: bool = False,
    minimum_ram_bytes: int | None = None,
    minimum_disk_bytes: int | None = None,
    os_release_path: str | Path = "/etc/os-release",
    home: str | Path | None = None,
    config_path: str | Path | None = None,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    if minimum_ram_bytes is not None and minimum_ram_bytes < 0:
        raise ValueError("minimum RAM must not be negative")
    if minimum_disk_bytes is not None and minimum_disk_bytes < 0:
        raise ValueError("minimum disk space must not be negative")
    runner = command_runner or _read_command
    home_path = Path(home) if home is not None else Path.home()
    workspace_path = (
        Path(workspace_root).expanduser()
        if workspace_root is not None
        else home_path / ".devrelay-x" / "workspaces"
    )
    control_path = Path(control_repository).expanduser() if control_repository else None
    platform_system = platform.system()
    release = _parse_os_release(Path(os_release_path))

    if platform_system != "Linux":
        os_check = _check(
            "operating_system",
            CheckStatus.FAIL,
            "This host is not Linux; it cannot establish Debian-node readiness",
            system=platform_system,
            machine=platform.machine(),
        )
    elif release.get("ID", "").lower() != "debian":
        os_check = _check(
            "operating_system",
            CheckStatus.FAIL,
            "This Linux host is not identified as Debian",
            system=platform_system,
            distribution=release.get("ID", "unknown"),
            version=release.get("VERSION_ID", "unknown"),
            machine=platform.machine(),
        )
    else:
        os_check = _check(
            "operating_system",
            CheckStatus.PASS,
            "Debian release information was detected",
            system=platform_system,
            distribution=release.get("ID"),
            version=release.get("VERSION_ID"),
            machine=platform.machine(),
        )

    disk_probe_path = workspace_path
    while not disk_probe_path.exists() and disk_probe_path != disk_probe_path.parent:
        disk_probe_path = disk_probe_path.parent
    torch_check = _probe_torch_runtime(runner)
    hardware = discover_node_hardware(disk_probe_path, command_runner=runner)
    ram = hardware["ram_bytes"]
    disk_free = hardware["disk_free_bytes"]
    ram_status = (
        CheckStatus.NOT_ASSESSED
        if ram is None
        else CheckStatus.PASS
        if minimum_ram_bytes is None or ram >= minimum_ram_bytes
        else CheckStatus.FAIL
    )
    disk_status = (
        CheckStatus.NOT_ASSESSED
        if disk_free is None or minimum_disk_bytes is None
        else CheckStatus.PASS
        if disk_free >= minimum_disk_bytes
        else CheckStatus.FAIL
    )
    gpu_check, devices = _probe_nvidia_runtime(hardware)

    checks = [
        os_check,
        _check(
            "python_version",
            CheckStatus.PASS if sys.version_info >= (3, 12) else CheckStatus.FAIL,
            (
                "Python meets the project minimum of 3.12"
                if sys.version_info >= (3, 12)
                else "Python is below the project minimum of 3.12"
            ),
            version=platform.python_version(),
            minimum="3.12",
        ),
        _check(
            "host_cpu",
            CheckStatus.PASS if hardware["cpu_count"] else CheckStatus.NOT_ASSESSED,
            "CPU identity and logical core count collected",
            model=hardware["cpu"],
            logical_cores=hardware["cpu_count"],
            architecture=platform.machine(),
        ),
        _check(
            "host_memory",
            ram_status,
            (
                "Installed RAM meets the configured minimum"
                if ram_status is CheckStatus.PASS
                else "RAM threshold is not configured or could not be read"
                if ram_status is CheckStatus.NOT_ASSESSED
                else "Installed RAM is below the configured minimum"
            ),
            total_bytes=ram,
            minimum_bytes=minimum_ram_bytes,
        ),
        _check(
            "workspace_disk",
            disk_status,
            (
                "Free disk space meets the configured minimum"
                if disk_status is CheckStatus.PASS
                else "Disk threshold is not configured or could not be read"
                if disk_status is CheckStatus.NOT_ASSESSED
                else "Free disk space is below the configured minimum"
            ),
            checked_path=str(disk_probe_path),
            total_bytes=hardware["disk_total_bytes"],
            free_bytes=disk_free,
            minimum_bytes=minimum_disk_bytes,
        ),
        gpu_check,
        _probe_cuda_toolkit(runner),
        torch_check,
        _probe_model_fit(model_profile, devices),
        _probe_systemd(runner),
        _probe_listeners(runner),
        _probe_firewall(runner),
        _probe_workspace(workspace_path, control_path),
        _probe_git_credentials(
            home_path,
            runner,
            Path(config_path) if config_path is not None else None,
        ),
        _check(
            "runtime_configuration",
            CheckStatus.WARN if runtime_configured else CheckStatus.NOT_ASSESSED,
            (
                "A validated loopback-compatible runtime configuration was supplied; "
                "no health or generation request was sent"
                if runtime_configured
                else (
                    "No runtime configuration was supplied; runtime availability "
                    "and inference compatibility remain unverified"
                )
            ),
            health_request_sent=False,
            model_request_sent=False,
        ),
    ]

    statuses = {check.status for check in checks}
    overall = (
        "blocked"
        if CheckStatus.FAIL in statuses
        else "needs_review"
        if CheckStatus.WARN in statuses or CheckStatus.NOT_ASSESSED in statuses
        else "ready"
    )
    risks = [
        {
            "check_id": check.check_id,
            "severity": (
                "blocker"
                if check.status is CheckStatus.FAIL
                else "review"
            ),
            "summary": check.summary,
        }
        for check in checks
        if check.status in {CheckStatus.FAIL, CheckStatus.WARN, CheckStatus.NOT_ASSESSED}
    ]
    body: dict[str, Any] = {
        "schema_version": 1,
        "report_id": uuid.uuid4().hex,
        "node_id": node_id,
        "scope": "local-host-read-only",
        "generated_at": datetime.now(UTC).isoformat(),
        "overall_status": overall,
        "host": {
            "system": platform_system,
            "distribution": release.get("ID", "unknown"),
            "distribution_version": release.get("VERSION_ID", "unknown"),
            "architecture": platform.machine(),
        },
        "checks": [check.to_dict() for check in checks],
        "risks": risks,
        "limitations": [
            "No remote host was contacted.",
            "No package was installed and no service was started or modified.",
            "No model was loaded or inference task was executed.",
            (
                "Model VRAM figures are estimates from supplied profile metadata, "
                "not a runtime benchmark."
            ),
            "Firewall rules are summarized, not proven safe by a complete policy audit.",
            "The SHA-256 digest detects report changes but is not a producer signature.",
        ],
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    body["evidence_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return body
