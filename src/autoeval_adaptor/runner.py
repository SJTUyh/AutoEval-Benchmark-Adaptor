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


def execute(
    prepared: PreparedRun,
    *,
    dry_run: bool = False,
    preflight: dict | None = None,
    adapter=None,
) -> dict:
    started_at = _now()

    if dry_run:
        _print_dry_run(prepared)
        return {
            "tool": prepared.tool,
            "subcommand": prepared.subcommand,
            "status": "dry_run",
            "exit_code": 0,
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
            "unified_result": None,
            "unified_result_path": None,
            "unified_result_error": None,
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

    unified_result, unified_result_path, unified_result_error = _collect_unified(
        prepared, adapter, output_dir
    )

    result = {
        "tool": prepared.tool,
        "subcommand": prepared.subcommand,
        "status": status,
        "exit_code": exit_code,
        "command": prepared.command,
        "cwd": str(prepared.cwd),
        "generated_config": str(prepared.config_path) if prepared.config_path else None,
        "ais_bench_work_dir": str(output_dir) if output_dir else None,
        "log_path": str(log_path),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration_sec,
        "error": error,
        "params": prepared.summary,
        "preflight": preflight,
        "unified_result": unified_result,
        "unified_result_path": unified_result_path,
        "unified_result_error": unified_result_error,
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


def _collect_unified(
    prepared: PreparedRun,
    adapter,
    output_dir: Path | None,
) -> tuple[dict | None, str | None, str | None]:
    """执行结束后提取统一结果；提取失败不影响测评状态。"""
    if adapter is None or output_dir is None:
        return None, None, None
    try:
        payload = adapter.collect_results(prepared, output_dir)
    except Exception as exc:  # noqa: BLE001 外部产物解析失败不应中断运行
        message = f"{type(exc).__name__}: {exc}"
        print(f"[adaptor] 统一结果提取失败: {message}", file=sys.stderr)
        return None, None, message
    if payload is None:
        return None, None, f"{prepared.tool}/{prepared.subcommand} 尚未实现统一结果提取"
    path = prepared.work_dir / "unified_result.json"
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        message = f"写入统一结果失败: {exc}"
        print(f"[adaptor] {message}", file=sys.stderr)
        return None, None, message
    return payload, str(path), None


def _run_streaming(prepared: PreparedRun, log_path: Path) -> tuple[int, bool]:
    with log_path.open("w", encoding="utf-8") as log:
        log.write("# command: " + " ".join(prepared.command) + "\n\n")
        log.flush()

        process = subprocess.Popen(
            prepared.command,
            cwd=str(prepared.cwd),
            env=prepared.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
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
                        process.kill()
                    continue
                if line is None:
                    break
                sys.stdout.write(line)
                sys.stdout.flush()
                log.write(line)
                log.flush()
        except KeyboardInterrupt:
            if process.poll() is None:
                process.kill()
            raise

        exit_code = process.wait()
        if timed_out:
            exit_code = 124
        return exit_code, timed_out


def _timestamp_dirs(path: Path) -> set[str]:
    if not path.is_dir():
        return set()
    return {
        item.name
        for item in path.iterdir()
        if item.is_dir() and TIMESTAMP_DIR.match(item.name)
    }


def _print_dry_run(prepared: PreparedRun) -> None:
    print("=== 生成的 AISBench 配置文件 ===")
    if prepared.config_path:
        print(f"# 目标路径: {prepared.config_path}")
    print(prepared.config_content or "<无>")
    print("=== 执行命令 ===")
    print(" ".join(prepared.command))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
