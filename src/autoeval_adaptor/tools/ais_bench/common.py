"""AISBench 适配器共用的运行参数与命令拼装。"""

from __future__ import annotations

import os
import pprint
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ...config import ParamReader
from ...discovery import resolve_root


@dataclass
class RuntimeOptions:
    work_dir: Path
    max_num_workers: int
    debug: bool
    reuse: str | None
    timeout: float | None
    monitor_port: int
    purge_exception_cases: bool
    ais_bench_root: Path
    python_executable: str
    extra_cli_args: list[str]


def take_runtime(reader: ParamReader, *, search_from: Path | None = None) -> RuntimeOptions:
    work_dir = reader.take("work_dir", required=True)
    max_num_workers = reader.take("max_num_workers", kind="int", default=1)
    debug = reader.take("debug", kind="bool", default=False)
    reuse = reader.take("reuse")
    timeout = reader.take("timeout", kind="float")
    monitor_port = reader.take("monitor_port", kind="int", default=0)
    purge_exception_cases = reader.take(
        "purge_exception_cases", kind="bool", default=False
    )
    python_executable = reader.take("python_executable", default=sys.executable)
    extra_cli_args = reader.take("extra_cli_args", kind="list_str", default=[])
    root = resolve_root(reader.take("ais_bench_root"), search_from)

    return RuntimeOptions(
        work_dir=Path(work_dir).expanduser().resolve(),
        max_num_workers=max_num_workers,
        debug=debug,
        reuse=reuse,
        timeout=float(timeout) if timeout is not None else None,
        monitor_port=monitor_port,
        purge_exception_cases=purge_exception_cases,
        ais_bench_root=root,
        python_executable=python_executable,
        extra_cli_args=list(extra_cli_args),
    )


def build_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(root) + (os.pathsep + existing if existing else "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def make_config_path(runtime: RuntimeOptions, tool: str, subcommand: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{tool}_{subcommand}_{stamp}_{os.getpid()}.py"
    return runtime.work_dir / ".adaptor" / filename


def build_command(
    runtime: RuntimeOptions,
    config_path: Path,
    mode: str,
    extra_args: list[str] | None = None,
) -> list[str]:
    command = [
        runtime.python_executable,
        "-m",
        "ais_bench.benchmark.cli.main",
        str(config_path),
        "-m",
        mode,
        "--work-dir",
        str(runtime.work_dir),
        "--max-num-workers",
        str(runtime.max_num_workers),
    ]
    if runtime.debug:
        command.append("--debug")
    if runtime.reuse:
        command.extend(["--reuse", runtime.reuse])
    if runtime.purge_exception_cases:
        command.append("--purge-exception-cases")
    if extra_args:
        command.extend(extra_args)
    command.extend(runtime.extra_cli_args)
    return command


def as_python(value: object) -> str:
    return pprint.pformat(value, width=100, sort_dicts=False)
