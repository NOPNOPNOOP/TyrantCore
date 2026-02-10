"""应用配置。基于 pydantic-settings，从 ~/.config/tyrant_core/.env 读取。"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path.home() / ".config" / "tyrant_core"
ENV_FILE = CONFIG_DIR / ".env"


class Settings(BaseSettings):
    """TyrantCore 全局配置。

    读取优先级（高→低）：
    1. 环境变量 ASA_*
    2. ~/.config/tyrant_core/.env
    3. 默认值
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_prefix="ASA_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Agent 模型配置 ─────────────────────────────────────────────────────

    model: str = Field(
        default="gemini-2.0-flash",
        description="LLM 模型名，如 claude-sonnet-4-5-20250929",
    )
    api_key: str = Field(
        default="",
        description="模型 API Key",
    )
    api_base: str = Field(
        default="",
        description="模型 API Base URL（OpenAI 兼容端点）",
    )

    # ── 系统配置 ───────────────────────────────────────────────────────────

    data_dir: str = Field(
        default="data",
        description="数据目录路径",
    )
    tyrant_node_id: str = Field(
        default="tyrant-00000000",
        description="Tyrant 节点 ID",
    )
    log_level: str = Field(
        default="INFO",
        description="日志级别",
    )

    def get_litellm_model(self) -> str:
        """返回 LiteLLM 格式的模型名。

        如果配置了 api_base（OpenAI 兼容端点），自动加 openai/ 前缀。
        """
        if self.api_base and not self.model.startswith(("openai/", "gemini")):
            return f"openai/{self.model}"
        return self.model

    def apply_env(self) -> None:
        """将配置注入到环境变量，供 LiteLLM / ADK 使用。"""
        if self.api_key:
            os.environ["OPENAI_API_KEY"] = self.api_key
        if self.api_base:
            os.environ["OPENAI_API_BASE"] = self.api_base


# 全局单例
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
