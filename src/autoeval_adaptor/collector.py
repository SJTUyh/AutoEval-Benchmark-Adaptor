"""独立于执行流程的统一结果收集。"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import ConfigError, ExecutionError
from .runner import TIMESTAMP_DIR
from .tools.base import PreparedRun


def resolve_result_dir(work_dir: Path, explicit: str | Path | None = None) -> Path:
    """按显式目录、执行记录、最新时间戳目录的顺序定位测评产物。"""
    work_dir = Path(work_dir)

    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_dir():
            raise ConfigError(f"指定的产物目录不存在或不是目录: {candidate}")
        if (candidate / "results").is_dir():
            return candidate
        nested = _timestamp_dirs(candidate)
        if nested:
            return _latest(candidate, nested)
        return candidate

    record_path = work_dir / "adaptor_result.json"
    record = _read_json(record_path) if record_path.is_file() else None
    if isinstance(record, dict):
        value = record.get("ais_bench_work_dir")
        if isinstance(value, str) and value:
            recorded = Path(value)
            if recorded.is_dir():
                return recorded

    nested = _timestamp_dirs(work_dir)
    if nested:
        return _latest(work_dir, nested)

    raise ConfigError(f"未找到可解析的测评产物目录: {work_dir}")


def collect_results(
    prepared: PreparedRun,
    adapter,
    *,
    result_dir: str | Path | None = None,
    output: str | Path | None = None,
) -> dict:
    """提取统一结果并落盘；同时回写 adaptor_result.json。"""
    resolved = resolve_result_dir(prepared.work_dir, result_dir)
    payload = adapter.collect_results(prepared, resolved)
    if payload is None:
        raise ConfigError(
            f"{prepared.tool}/{prepared.subcommand} 尚未实现统一结果提取"
        )

    output_path = (
        Path(output).expanduser().resolve()
        if output is not None
        else prepared.work_dir / "unified_result.json"
    )
    _write_json(output_path, payload)
    _update_record(prepared.work_dir / "adaptor_result.json", payload, output_path)
    return {
        "result": payload,
        "path": str(output_path),
        "source_dir": str(resolved),
    }


def _update_record(record_path: Path, payload: dict, output_path: Path) -> None:
    if not record_path.is_file():
        return
    record = _read_json(record_path)
    if not isinstance(record, dict):
        return
    record["unified_result"] = payload
    record["unified_result_path"] = str(output_path)
    record["unified_result_error"] = None
    _write_json(record_path, record)


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ExecutionError(f"写入统一结果失败: {exc}") from exc


def _timestamp_dirs(path: Path) -> set[str]:
    if not path.is_dir():
        return set()
    return {
        item.name
        for item in path.iterdir()
        if item.is_dir() and TIMESTAMP_DIR.match(item.name)
    }


def _latest(base: Path, names: set[str]) -> Path:
    def mtime(name: str) -> float:
        try:
            return (base / name).stat().st_mtime
        except OSError:
            return 0.0

    return base / max(names, key=mtime)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None
