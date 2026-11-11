"""适配器基类与执行计划数据结构。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..docker_env import ContainerRun, wrap_exec


@dataclass
class PreparedRun:
    """一个已校验、待执行的测评调用。

    ``command`` 始终是**容器内（或本机）的原生命令**，不包含 docker 前缀；
    需要实际执行时调用 :meth:`exec_command`。
    """

    tool: str
    subcommand: str
    work_dir: Path
    cwd: Path | str
    env: dict[str, str]
    command: list[str]
    python_executable: str | None = None
    config_path: Path | None = None
    config_content: str | None = None
    timeout: float | None = None
    mode: str | None = None
    summary: dict = field(default_factory=dict)
    preflight_context: dict = field(default_factory=dict)
    container: ContainerRun | None = None

    def exec_command(self, argv: list[str] | None = None) -> list[str]:
        native = list(self.command if argv is None else argv)
        if self.container is not None:
            return wrap_exec(self.container, native)
        return native


class BaseAdapter(ABC):
    tool = ""
    subcommand = ""

    @abstractmethod
    def prepare(self, params: dict, *, container: ContainerRun | None = None) -> PreparedRun:
        """校验 params 并生成待执行任务。"""

    def preflight(self, prepared: PreparedRun):
        """按工具校验参数与目标环境是否兼容；默认无预检项。"""
        from ..preflight import PreflightReport

        report = PreflightReport(tool=prepared.tool, subcommand=prepared.subcommand)
        report.add("工具预检", "pass", "该工具未定义环境预检项")
        return report
