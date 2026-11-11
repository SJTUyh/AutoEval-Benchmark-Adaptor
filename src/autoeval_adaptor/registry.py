"""tool + subcommand 到适配器的注册表。"""

from __future__ import annotations

from typing import Callable, TypeVar

from .errors import ConfigError

_ADAPTERS: dict[tuple[str, str], type] = {}

T = TypeVar("T")


def register(tool: str, subcommand: str) -> Callable[[T], T]:
    def wrapper(cls: T) -> T:
        key = (tool, subcommand)
        if key in _ADAPTERS:
            raise RuntimeError(f"适配器重复注册: {tool}/{subcommand}")
        _ADAPTERS[key] = cls  # type: ignore[assignment]
        return cls

    return wrapper


def get_adapter(tool: str, subcommand: str):
    from . import tools  # noqa: F401  确保内置适配器完成注册

    try:
        adapter_cls = _ADAPTERS[(tool, subcommand)]
    except KeyError:
        available = ", ".join(f"{t}/{s}" for t, s in sorted(_ADAPTERS)) or "<无>"
        raise ConfigError(
            f"不支持的 tool/subcommand: {tool}/{subcommand}，已支持: {available}"
        ) from None
    return adapter_cls()


def registered_adapters() -> list[tuple[str, str]]:
    """返回已注册的 (tool, subcommand) 列表。"""
    from . import tools  # noqa: F401

    return sorted(_ADAPTERS)
