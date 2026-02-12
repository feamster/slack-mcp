"""Configuration management for multi-workspace Slack support."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WorkspaceConfig:
    """Configuration for a single Slack workspace."""
    key: str
    name: str
    token: str
    priority: int = 1


@dataclass
class Config:
    """Global configuration with all workspaces."""
    workspaces: dict[str, WorkspaceConfig]
    default_workspace: Optional[str] = None

    def get_workspace(self, key: Optional[str] = None) -> WorkspaceConfig:
        """Get a workspace by key, or the default."""
        if key is None:
            key = self.default_workspace
        if key is None:
            # Return first workspace
            return next(iter(self.workspaces.values()))
        if key not in self.workspaces:
            raise ValueError(f"Unknown workspace: {key}")
        return self.workspaces[key]

    def list_workspaces(self) -> list[WorkspaceConfig]:
        """List all workspaces sorted by priority."""
        return sorted(self.workspaces.values(), key=lambda w: w.priority)


def load_config() -> Config:
    """
    Load configuration from file or environment variables.

    Priority:
    1. Config file at ~/.config/slack-mcp/config.json
    2. Environment variables (SLACK_TOKEN_* pattern)
    3. Single SLACK_USER_TOKEN environment variable
    """
    workspaces: dict[str, WorkspaceConfig] = {}
    default_workspace: Optional[str] = None

    # Try config file first - use ~/.mcp-auth/slack/ to match other MCP servers
    config_path = Path.home() / ".mcp-auth" / "slack" / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            data = json.load(f)

        for key, ws_data in data.get("workspaces", {}).items():
            workspaces[key] = WorkspaceConfig(
                key=key,
                name=ws_data.get("name", key),
                token=ws_data["token"],
                priority=ws_data.get("priority", 1),
            )
        default_workspace = data.get("default_workspace")

    # Check for SLACK_TOKEN_* environment variables
    for key, value in os.environ.items():
        if key.startswith("SLACK_TOKEN_") and key != "SLACK_TOKEN_":
            ws_key = key[12:].lower()  # Remove SLACK_TOKEN_ prefix
            if ws_key not in workspaces:
                workspaces[ws_key] = WorkspaceConfig(
                    key=ws_key,
                    name=ws_key.replace("_", " ").title(),
                    token=value,
                    priority=len(workspaces) + 1,
                )

    # Fallback to single SLACK_USER_TOKEN
    if not workspaces:
        token = os.environ.get("SLACK_USER_TOKEN")
        if token:
            workspaces["default"] = WorkspaceConfig(
                key="default",
                name="Slack",
                token=token,
                priority=1,
            )
            default_workspace = "default"

    if not workspaces:
        raise ValueError(
            "No Slack configuration found. Either:\n"
            "1. Create ~/.mcp-auth/slack/config.json\n"
            "2. Set SLACK_TOKEN_<name> environment variables\n"
            "3. Set SLACK_USER_TOKEN environment variable"
        )

    return Config(workspaces=workspaces, default_workspace=default_workspace)


# Global config instance (lazy loaded)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global config instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
