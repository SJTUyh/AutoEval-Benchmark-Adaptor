"""Harbor Agent 测评产物到统一结果的解析。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..base import PreparedRun

SCHEMA_VERSION = 1
CANCELLED_ERROR_TYPE = "CancelledError"

_SUMMARY_KEYS = (
    "total_count",
    "n_errors",
    "n_total_trials",
    "pass_at_k",
    "reward_distribution",
    "exception_distribution",
)


def collect_harbor_results(prepared: PreparedRun, output_dir: Path) -> dict:
    """从 AISBench harbor_agent 时间戳目录提取统一结果。"""
    work_dir = Path(output_dir)
    model_abbr = prepared.summary.get("abbr")
    dataset_abbr = prepared.summary.get("dataset_abbr")

    model_dir = work_dir / "results" / str(model_abbr) if model_abbr else None
    dataset_dir = (
        model_dir / str(dataset_abbr)
        if model_dir is not None and dataset_abbr
        else None
    )
    result_json_path = (
        model_dir / f"{dataset_abbr}.json"
        if model_dir is not None and dataset_abbr
        else None
    )
    job_dir = dataset_dir / "details" if dataset_dir is not None else None

    summary_doc = _read_json(result_json_path) if result_json_path else None
    trials = (
        _collect_cases(job_dir)
        if job_dir is not None and job_dir.is_dir()
        else []
    )

    total_score = _score_from_summary(summary_doc)
    score_source = "ais_bench_results_json" if total_score is not None else None
    if total_score is None and trials:
        total_score = _score_from_trials(trials)
        score_source = "trials"

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": prepared.tool,
        "subcommand": prepared.subcommand,
        "total_score": total_score,
        "score_source": score_source,
        "case_count": len(trials),
        "summary": _build_summary(summary_doc),
        "cases": trials,
        "source": {
            "result_json": (
                str(result_json_path)
                if result_json_path is not None and result_json_path.is_file()
                else None
            ),
            "job_dir": (
                str(job_dir) if job_dir is not None and job_dir.is_dir() else None
            ),
        },
    }


def _collect_cases(job_dir: Path) -> list[dict]:
    trial_dirs = sorted(_case_dirs(job_dir))
    attempts: dict[str, int] = {}
    cases: list[dict] = []
    for trial_dir in trial_dirs:
        result = _read_json(trial_dir / "result.json")
        case_id = _case_id(trial_dir, result)
        attempt = attempts.get(case_id, 0) + 1
        attempts[case_id] = attempt
        cases.append(_build_case(trial_dir, result, case_id, attempt))
    return cases


def _case_dirs(job_dir: Path) -> set[Path]:
    """兼容两种 Harbor 布局：trial_* 目录，以及以任务名命名的 case 目录。"""
    candidates = {
        path for path in job_dir.glob("trial_*") if path.is_dir()
    }
    for path in job_dir.iterdir():
        if path.is_dir() and (path / "result.json").is_file():
            candidates.add(path)
    return candidates


def _build_case(
    trial_dir: Path,
    result: dict | None,
    case_id: str,
    attempt: int,
) -> dict:
    if result is None:
        return {
            "case_id": case_id,
            "trial_name": trial_dir.name,
            "trial_dir": trial_dir.name,
            "attempt": attempt,
            "status": "other",
            "score": None,
            "started_at": None,
            "finished_at": None,
            "duration_sec": None,
            "input_tokens": None,
            "output_tokens": None,
            "exception_type": None,
            "exception_message": None,
        }

    exception_info = result.get("exception_info")
    started_at = result.get("started_at")
    finished_at = result.get("finished_at")
    input_tokens, output_tokens = _tokens_from_result(result)

    return {
        "case_id": case_id,
        "trial_name": result.get("trial_name") or trial_dir.name,
        "trial_dir": trial_dir.name,
        "attempt": attempt,
        "status": _case_status(exception_info),
        "score": _score_from_result(result),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": _duration_sec(started_at, finished_at),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "exception_type": (
            exception_info.get("exception_type")
            if isinstance(exception_info, dict)
            else None
        ),
        "exception_message": (
            exception_info.get("exception_message")
            if isinstance(exception_info, dict)
            else None
        ),
    }


def _case_id(trial_dir: Path, result: dict | None) -> str:
    if result is not None:
        task_name = result.get("task_name")
        if isinstance(task_name, str) and task_name:
            return task_name
    name = _task_name_from_config(trial_dir)
    if name:
        return name
    if result is not None:
        trial_name = result.get("trial_name")
        if isinstance(trial_name, str) and trial_name:
            return trial_name
    return trial_dir.name


def _task_name_from_config(trial_dir: Path) -> str | None:
    data = _read_json(trial_dir / "config.json")
    if not isinstance(data, dict):
        return None
    task = data.get("task")
    if not isinstance(task, dict):
        return None
    path = task.get("path")
    if isinstance(path, str) and path:
        return Path(path).name
    name = task.get("name")
    return name if isinstance(name, str) and name else None


def _case_status(exception_info: object) -> str:
    if exception_info is None:
        return "success"
    if isinstance(exception_info, dict):
        if exception_info.get("exception_type") == CANCELLED_ERROR_TYPE:
            return "other"
    return "failed"


def _score_from_result(result: dict) -> float | None:
    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict):
        return None
    rewards = verifier.get("rewards")
    if not isinstance(rewards, dict):
        return None
    return _as_float(rewards.get("reward"))


def _score_from_summary(summary_doc: dict | None) -> float | None:
    if not isinstance(summary_doc, dict):
        return None
    return _as_float(summary_doc.get("avg_score"))


def _score_from_trials(trials: list[dict]) -> float | None:
    if not trials:
        return None
    values = []
    for case in trials:
        value = case.get("score")
        values.append(
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else 0.0
        )
    return round(sum(values) / len(values), 4)


def _build_summary(summary_doc: dict | None) -> dict:
    if not isinstance(summary_doc, dict):
        return {}
    return {
        key: summary_doc[key]
        for key in _SUMMARY_KEYS
        if key in summary_doc and summary_doc[key] is not None
    }


def _tokens_from_result(result: dict) -> tuple[int | None, int | None]:
    contexts = []
    agent_result = result.get("agent_result")
    if isinstance(agent_result, dict):
        contexts.append(agent_result)
    step_results = result.get("step_results")
    if isinstance(step_results, list):
        for step in step_results:
            if isinstance(step, dict) and isinstance(step.get("agent_result"), dict):
                contexts.append(step["agent_result"])
    if not contexts:
        return None, None
    return (
        _token_sum(contexts, "n_input_tokens"),
        _token_sum(contexts, "n_output_tokens"),
    )


def _token_sum(contexts: list[dict], key: str) -> int:
    total = 0
    for context in contexts:
        value = context.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def _duration_sec(started_at: object, finished_at: object) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        start = _parse_iso(started_at)
        end = _parse_iso(finished_at)
        return round((end - start).total_seconds(), 3)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: object) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None
