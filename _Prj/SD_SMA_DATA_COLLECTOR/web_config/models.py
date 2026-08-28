from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConfigValidateRequest(BaseModel):
    payload: dict[str, Any]


class ConfigExportRequest(BaseModel):
    payload: dict[str, Any]
    filename: str = Field(default="collector_config_export.json", min_length=1)


class ConfigWriteRequest(BaseModel):
    payload: dict[str, Any]
    filename: str = Field(..., min_length=1)


class OpcUaConnectRequest(BaseModel):
    host: str = Field(..., min_length=1)
    port: int = Field(default=4840, ge=1, le=65535)


class CollectorStartRequest(BaseModel):
    filename: str = Field(..., min_length=1)


class CollectorStartupSettingsRequest(BaseModel):
    auto_start_enabled: bool = False
    auto_start_config: str = ""
    auto_start_delay_seconds: int = Field(default=3, ge=0, le=300)

