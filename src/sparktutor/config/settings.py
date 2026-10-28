"""Configuration model for SparkTutor."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    AUTO = "auto"
    LAKEHOUSE = "lakehouse"
    LOCAL = "local"
    DRY_RUN = "dry_run"
    DATABRICKS = "databricks"
    ISOLATED = "isolated"


class IsolatedExecutionConfig(BaseModel):
    """Administrator-owned limits; never accepted from browser settings."""
    enabled: bool = False
    image: str = Field(default="sparktutor-executor:4.1.1", pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}$")
    docker_executable: str = "docker"
    cpus: float = Field(default=2.0, gt=0, le=32)
    memory: str = Field(default="3g", pattern=r"^[1-9][0-9]*[kKmMgG]$")
    pids_limit: int = Field(default=512, ge=64, le=4096)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=32)
    max_jobs_per_user: int = Field(default=1, ge=1, le=8)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    output_limit_bytes: int = Field(default=1_048_576, ge=1024, le=8_388_608)
    tmpfs_mb: int = Field(default=1024, ge=128, le=4096)


class DatabricksConfig(BaseModel):
    host: Optional[str] = None
    token: Optional[str] = None
    cluster_id: Optional[str] = None
    profile: Optional[str] = None

    def build_spark_remote(self) -> Optional[str]:
        """Build a Spark Connect URL for Databricks, or None if fields are missing."""
        if self.host and self.token and self.cluster_id:
            h = self.host.rstrip("/")
            return f"sc://{h}:443/;use_ssl=true;token={self.token};x-databricks-cluster-id={self.cluster_id}"
        return None


class DockerConfig(BaseModel):
    container_name: str = "spark-master-41"
    spark_master_url: str = "spark://localhost:7078"


class ClaudeConfig(BaseModel):
    api_key: Optional[str] = Field(default=None)
    model: str = "claude-sonnet-4-6"

    def get_api_key(self) -> Optional[str]:
        return self.api_key or os.environ.get("ANTHROPIC_API_KEY")

    def get_model(self) -> str:
        return os.environ.get("SPARKTUTOR_CLAUDE_MODEL") or self.model


class Settings(BaseModel):
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    docker: DockerConfig = Field(default_factory=DockerConfig)
    databricks: DatabricksConfig = Field(default_factory=DatabricksConfig)
    isolated_execution: IsolatedExecutionConfig = Field(default_factory=IsolatedExecutionConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    timeout_seconds: int = 120
    data_dir: Path = Path.home() / ".sparktutor"
    lakehouse_path: Optional[str] = None

    @classmethod
    def load(cls) -> "Settings":
        config_path = Path.home() / ".sparktutor" / "config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        # Environment variable overrides (set by VS Code extension)
        env_mode = os.environ.get("SPARKTUTOR_EXECUTION_MODE")
        if env_mode:
            data["execution_mode"] = env_mode
        env_lh_path = os.environ.get("SPARKTUTOR_LAKEHOUSE_PATH")
        if env_lh_path:
            data["lakehouse_path"] = env_lh_path

        # Databricks env var overrides
        db = data.get("databricks", {})
        for key, env_name in [
            ("host", "SPARKTUTOR_DATABRICKS_HOST"),
            ("token", "SPARKTUTOR_DATABRICKS_TOKEN"),
            ("cluster_id", "SPARKTUTOR_DATABRICKS_CLUSTER_ID"),
            ("profile", "SPARKTUTOR_DATABRICKS_PROFILE"),
        ]:
            val = os.environ.get(env_name)
            if val:
                db[key] = val
        if db:
            data["databricks"] = db

        isolated = data.get("isolated_execution", {})
        for key in IsolatedExecutionConfig.model_fields:
            value = os.environ.get("SPARKTUTOR_ISOLATED_" + key.upper())
            if value is not None:
                isolated[key] = value
        if isolated:
            data["isolated_execution"] = isolated

        return cls(**data)

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.data_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(self.model_dump(mode="json"), f, default_flow_style=False)
