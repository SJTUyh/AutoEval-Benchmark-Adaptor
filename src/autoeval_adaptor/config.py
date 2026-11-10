"""统一 JSON 配置的加载与参数校验。

顶层只允许 tool、subcommand、params 三个字段，子命令参数全部位于 params 内。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError

TOP_LEVEL_KEYS = ("tool", "subcommand", "params")
MISSING = object()

_KIND_NAMES = {
    "str": "字符串",
    "int": "整数",
    "float": "数字",
    "bool": "布尔值",
    "dict": "JSON 对象",
    "list": "JSON 数组",
    "list_str": "字符串数组",
    "any": "任意 JSON 值",
}


@dataclass(frozen=True)
class Request:
    tool: str
    subcommand: str
    params: dict[str, Any]


def load_request(path: str | Path) -> Request:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在: {config_path}")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件 {config_path} 不是合法 JSON: {exc}") from exc
    return parse_request(raw, source=str(config_path))


def parse_request(raw: Any, source: str = "<json>") -> Request:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source} 顶层必须是 JSON 对象，实际为 {type(raw).__name__}")

    extra = [key for key in raw if key not in TOP_LEVEL_KEYS]
    if extra:
        raise ConfigError(
            f"{source} 顶层只允许 tool、subcommand、params，发现多余字段: {', '.join(map(str, extra))}"
        )

    missing = [key for key in TOP_LEVEL_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"{source} 缺少顶层字段: {', '.join(missing)}")

    tool = _require_non_empty_str(raw["tool"], "tool", source)
    subcommand = _require_non_empty_str(raw["subcommand"], "subcommand", source)
    params = raw["params"]
    if not isinstance(params, dict):
        raise ConfigError(f"{source} 的 params 必须是 JSON 对象，实际为 {type(params).__name__}")

    return Request(tool=tool, subcommand=subcommand, params=dict(params))


def _require_non_empty_str(value: Any, field_name: str, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{source} 的 {field_name} 必须是非空字符串")
    return value.strip()


class ParamReader:
    """按字段声明逐个取出参数，未声明的残留字段会在 finish() 时报错。"""

    def __init__(self, params: dict[str, Any], where: str = "params"):
        self._values = dict(params)
        self._where = where

    def take(
        self,
        key: str,
        *,
        kind: str = "str",
        default: Any = MISSING,
        required: bool = False,
        choices: tuple[str, ...] | None = None,
    ) -> Any:
        if key not in self._values:
            if required and default is MISSING:
                raise ConfigError(f"{self._where} 缺少必填参数: {key}")
            return None if default is MISSING else default

        value = self._values.pop(key)
        if value is None:
            if required:
                raise ConfigError(f"{self._where} 缺少必填参数: {key}")
            return None if default is MISSING else default

        self._check_type(key, value, kind)
        if choices is not None and value not in choices:
            raise ConfigError(
                f"{self._where}.{key} 取值必须是 {', '.join(choices)} 之一，实际为 {value!r}"
            )
        return value

    def take_optional(self, key: str, *, kind: str = "str") -> Any:
        """存在且非 null 时返回校验后的值，否则返回 MISSING。"""
        if key not in self._values:
            return MISSING
        value = self._values.pop(key)
        if value is None:
            return MISSING
        self._check_type(key, value, kind)
        return value

    def finish(self) -> None:
        if self._values:
            unknown = ", ".join(sorted(self._values))
            raise ConfigError(f"{self._where} 存在未知参数: {unknown}")

    def _check_type(self, key: str, value: Any, kind: str) -> None:
        if not _matches_kind(value, kind):
            raise ConfigError(
                f"{self._where}.{key} 期望{_KIND_NAMES.get(kind, kind)}，"
                f"实际为 {type(value).__name__}: {value!r}"
            )


def _matches_kind(value: Any, kind: str) -> bool:
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "dict":
        return isinstance(value, dict)
    if kind == "list":
        return isinstance(value, list)
    if kind == "list_str":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if kind == "any":
        return True
    raise ValueError(f"未知的参数类型声明: {kind}")
