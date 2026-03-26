"""SQLite Store 的模型与行转换辅助。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any


def dt_to_str(value: datetime | None) -> str | None:
    """将 datetime 统一序列化为 UTC ISO 8601 字符串。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def normalize_value(value: Any) -> Any:
    """把 Enum / datetime / Pydantic 对象规整为数据库可写值。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return dt_to_str(value)
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_value(item) for key, item in value.items()}
    return value


def model_to_row(model: Any) -> dict[str, Any]:
    """Pydantic 模型转数据库行。"""
    return model.model_dump(mode="json")


def row_to_model(model_cls, row: Any):
    """数据库行转回 Pydantic 模型。"""
    if row is None:
        return None
    if hasattr(row, "_mapping"):
        payload = dict(row._mapping)
    else:
        payload = dict(row)
    return model_cls.model_validate(payload)

