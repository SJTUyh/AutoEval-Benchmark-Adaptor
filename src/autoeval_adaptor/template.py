"""内置配置模板的加载与合并。

模板按 tool/subcommand 分组存放：templates/<tool>/<subcommand>.json。
合并规则：以模板为底，用户配置覆盖同名参数；dict 递归合并，list/标量整体替换。
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .config import TOP_LEVEL_KEYS
from .errors import ConfigError


@dataclass(frozen=True)
class LoadedTemplate:
    params: dict[str, Any]
    source: str


def load_template(
    tool: str,
    subcommand: str,
    override: str | Path | None = None,
) -> LoadedTemplate:
    if override:
        path = Path(override)
        if not path.is_file():
            raise ConfigError(f"配置模板不存在: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置模板 {path} 不是合法 JSON: {exc}") from exc
        source = str(path)
    else:
        resource = resources.files("autoeval_adaptor").joinpath(
            "templates", tool, f"{subcommand}.json"
        )
        if not resource.is_file():
            raise ConfigError(f"未找到内置配置模板: {tool}/{subcommand}")
        try:
            raw = json.loads(resource.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"内置配置模板 {tool}/{subcommand} 不是合法 JSON: {exc}") from exc
        source = f"builtin:{tool}/{subcommand}"

    if not isinstance(raw, dict):
        raise ConfigError(f"配置模板 {source} 顶层必须是 JSON 对象")

    extra = [key for key in raw if key not in TOP_LEVEL_KEYS]
    if extra:
        raise ConfigError(
            f"配置模板 {source} 顶层只允许 tool、subcommand、params，发现: {', '.join(extra)}"
        )
    missing = [key for key in TOP_LEVEL_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"配置模板 {source} 缺少字段: {', '.join(missing)}")

    if raw["tool"] != tool or raw["subcommand"] != subcommand:
        raise ConfigError(
            f"配置模板 {source} 属于 {raw['tool']}/{raw['subcommand']}，"
            f"与请求的 {tool}/{subcommand} 不匹配"
        )
    params = raw["params"]
    if not isinstance(params, dict):
        raise ConfigError(f"配置模板 {source} 的 params 必须是 JSON 对象")

    return LoadedTemplate(params=deepcopy(params), source=source)


def merge_params(template: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """模板为默认值，用户配置优先；dict 递归合并。"""
    merged = deepcopy(template)
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_params(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
