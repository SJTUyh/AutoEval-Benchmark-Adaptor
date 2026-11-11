"""执行测评任务并落盘结果。"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .errors import ExecutionError
from .tools.base import PreparedRun

TIMESTAMP_DIR = re.compile(r"^\d{8}_\d{6}$")


def execute(prepared: PreparedRun, *, dry_run: bool = False, preflight: dict | None = None) -> dict:
    started_at = _now()

    if dry_run:
        _print_dry_run(prepared)
        return {
            "tool": prepared.tool,
            "subcommand": prepared.subcommand,
            "status": "dry_run",
            "exit_code": 0,
            "execution": "docker" if prepared.container else "local",
            "container": prepared.container.name if prepared.container else None,
            "image": prepared.container.image if prepared.container else None,
            "command": prepared.command,
            "generated_config": (
                str(prepared.config_path) if prepared.config_path else None
            ),
            "config_content": prepared.config_content,
            "started_at": started_at,
            "finished_at": _now(),
            "duration_sec": 0.0,
            "error": None,
            "preflight": preflight,
        }

    try:
        prepared.work_dir.mkdir(parents=True, exist_ok=True)
        if prepared.config_path and prepared.config_content is not None:
            prepared.config_path.parent.mkdir(parents=True, exist_ok=True)
            prepared.config_path.write_text(prepared.config_content, encoding="utf-8")
    except OSError as exc:
        raise ExecutionError(f"准备运行目录失败: {exc}") from exc

    log_path = prepared.work_dir / "adaptor.log"
    before = _timestamp_dirs(prepared.work_dir)

    start = time.monotonic()
    status = "error"
    exit_code = 3
    error: str | None = None

    try:
        exit_code, timed_out = _run_streaming(prepared, log_path)
        if timed_out:
            status = "timeout"
            error = f"执行超过 {prepared.timeout} 秒，已终止进程"
        elif exit_code == 0:
            status = "success"
        else:
            status = "failed"
    except OSError as exc:
        error = str(exc)

    finished_at = _now()
    duration_sec = round(time.monotonic() - start, 3)
    all_dirs = _timestamp_dirs(prepared.work_dir)
    new_dirs = sorted(all_dirs - before)
    output_dir: Path | None = None
    if new_dirs:
        output_dir = prepared.work_dir / new_dirs[-1]
    elif exit_code == 0 and all_dirs:
        # --reuse 时 AISBench 复用已有时间戳目录，不会新建目录。
        latest = max(
            all_dirs,
            key=lambda name: (prepared.work_dir / name).stat().st_mtime,
        )
        output_dir = prepared.work_dir / latest

    result = {
        "tool": prepared.tool,
        "subcommand": prepared.subcommand,
        "status": status,
        "exit_code": exit_code,
        "execution": "docker" if prepared.container else "local",
        "container": prepared.container.name if prepared.container else None,
        "image": prepared.container.image if prepared.container else None,
        "command": prepared.command,
        "cwd": prepared.container.workdir if prepared.container else str(prepared.cwd),
        "generated_config": str(prepared.config_path) if prepared.config_path else None,
        "ais_bench_work_dir": str(output_dir) if output_dir else None,
        "log_path": str(log_path),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration_sec,
        "error": error,
        "params": prepared.summary,
        "preflight": preflight,
    }

    result_path = prepared.work_dir / "adaptor_result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result["result_path"] = str(result_path)
    print(
        f"[adaptor] status={status} exit_code={exit_code} result={result_path}",
        file=sys.stderr if status in ("failed", "timeout", "error") else sys.stdout,
    )
    return result


def _run_streaming(prepared: PreparedRun, log_path: Path) -> tuple[int, bool]:
    argv = prepared.exec_command()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("# command: " + " ".join(argv) + "\n")
        if prepared.container:
            log.write(
                f"# container: {prepared.container.name} "
                f"image={prepared.container.image}\n"
            )
        log.write("\n")
        log.flush()

        popen_kwargs = {}
        if prepared.container is None:
            popen_kwargs = {"cwd": str(prepared.cwd), "env": prepared.env}

        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **popen_kwargs,
        )

        lines: queue.Queue[str | None] = queue.Queue()

        def pump() -> None:
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        threading.Thread(target=pump, daemon=True).start()

        deadline = time.monotonic() + prepared.timeout if prepared.timeout else None
        timed_out = False

        try:
            while True:
                if process.poll() is not None and lines.empty():
                    break
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    if (
                        deadline is not None
                        and time.monotonic() > deadline
                        and process.poll() is None
                    ):
                        timed_out = True
                        _terminate(prepared, process)
                    continue
                if line is None:
                    break
                sys.stdout.write(line)
                sys.stdout.flush()
                log.write(line)
                log.flush()
        except KeyboardInterrupt:
            _terminate(prepared, process)
            raise

        exit_code = process.wait()
        if timed_out:
            exit_code = 124
        return exit_code, timed_out


def _terminate(prepared: PreparedRun, process: subprocess.Popen) -> None:
    """终止执行；容器模式下先让容器内进程退出，再结束本地 docker 客户端。"""
    if prepared.container is not None:
        try:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    prepared.container.name,
                    "pkill",
                    "-f",
                    "ais_bench.benchmark.cli.main",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if process.poll() is None:
        process.kill()


def _timestamp_dirs(path: Path) -> set[str]:
    if not path.is_dir():
        return set()
    return {
        item.name
        for item in path.iterdir()
        if item.is_dir() and TIMESTAMP_DIR.match(item.name)
    }


def _print_dry_run(prepared: PreparedRun) -> None:
    print("=== 执行环境 ===")
    if prepared.container:
        print("execution: docker")
        print(f"image: {prepared.container.image}")
        print(f"container: {prepared.container.name}")
        print(f"isolation: {prepared.container.isolation}")
        print(f"workdir: {prepared.container.workdir}")
    else:
        print("execution: local")
    print("=== 生成的 AISBench 配置文件 ===")
    if prepared.config_path:
        print(f"# 目标路径: {prepared.config_path}")
    print(prepared.config_content or "<无>")
    print("=== 执行命令 ===")
    print(" ".join(prepared.exec_command()))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
