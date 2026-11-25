from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from drx.runtime.config import RuntimeConfig


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path = Path.home() / ".devrelay-x" / "workspaces"
    allow_control_repository: bool = False


class TestConfig(BaseModel):
    __test__ = False

    model_config = ConfigDict(extra="forbid")

    framework: Literal["auto", "pytest", "unittest"] = "auto"
    timeout_seconds: int = Field(default=600, gt=0, le=7200)


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_local_commit: bool = True
    allow_remote_push: Literal[False] = False


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: AgentConfig
    runtime: RuntimeConfig
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    test: TestConfig = Field(default_factory=TestConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)


def load_config(path: str | Path = ".drx/config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"DevRelay-X config not found: {config_path}")
    with config_path.open(encoding="utf-8") as config_file:
        raw_config = yaml.safe_load(config_file)
    if not isinstance(raw_config, dict):
        raise ValueError("configuration must be a YAML mapping")
    return AppConfig.model_validate(raw_config)
