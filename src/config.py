"""Configuration module — loads settings from .env file."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    tas_base_url: str = Field(
        default="https://tas.vghtpe.gov.tw/tas/",
        description="Base URL of the TAS system",
    )

    llm_provider: str = Field(
        default="gemini",
        description="LLM provider: gemini | openai | anthropic",
    )
    llm_api_key: str = Field(
        default="",
        description="API key for the chosen LLM provider",
    )
    llm_model: str = Field(
        default="gemini-2.0-flash",
        description="Model name for the LLM provider",
    )

    dry_run: bool = Field(
        default=False,
        description="When True, log actions but do not fill forms",
    )

    browser_data_dir: str = Field(
        default="./browser_data",
        description="Directory for Playwright persistent browser profile",
    )

    ui_port: int = Field(
        default=8550,
        description="Port for the web UI",
    )

    audit_log_path: str = Field(
        default="./audit_log.jsonl",
        description="Path to the JSON-lines audit log file",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Singleton instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    global _settings
    if _settings is None:
        # Look for .env in the project root (parent of src/)
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            _settings = Settings(_env_file=str(env_path))
        else:
            _settings = Settings()
    return _settings
