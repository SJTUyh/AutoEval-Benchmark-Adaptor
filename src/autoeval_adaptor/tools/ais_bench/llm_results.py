"""常规 LLM 精度测评产物到统一结果的解析。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..base import PreparedRun

SCHEMA_VERSION = 1
_PARTIAL_SUFFIX = re.compile(r"_\d+$")


def collect_llm_results(prepared: PreparedRun, output_dir: Path) -> dict:
    """从 AISBench llm_accuracy 时间戳目录提取统一结果。"""
    work_dir = Path(output_dir)
    model_abbr = prepared.summary.get("abbr")
    model_results_dir = (
        work_dir / "results" / str(model_abbr) if model_abbr else None
    )
    model_predictions_dir = (
        work_dir / "predictions" / str(model_abbr) if model_abbr else None
    )

    result_paths = _glob_json(model_results_dir)
    prediction_paths = _glob_jsonl(model_predictions_dir, failed=False)
    failed_paths = _glob_jsonl(model_predictions_dir, failed=True)

    datasets = _dataset_names(result_paths, prediction_paths, failed_paths)

    cases: list[dict] = []
    dataset_summaries: list[dict] = []
    weighted_score = 0.0
    total_weight = 0.0
    available_scores: list[float] = []

    for dataset in datasets:
        result_doc = (
            _read_json(model_results_dir / f"{dataset}.json")
            if model_results_dir is not None
            else None
        )
        records = _read_jsonl(
            model_predictions_dir / f"{dataset}.jsonl"
            if model_predictions_dir is not None
            else None
        )
        failed_records = _read_jsonl(
            model_predictions_dir / f"{dataset}_failed.jsonl"
            if model_predictions_dir is not None
            else None
        )

        dataset_cases = _build_dataset_cases(dataset, result_doc, records, failed_records)
        cases.extend(dataset_cases)

        raw_accuracy = _as_float(
            result_doc.get("accuracy") if isinstance(result_doc, dict) else None
        )
        details = (
            result_doc.get("details")
            if isinstance(result_doc, dict)
            else None
        )
        score = _dataset_score(details, raw_accuracy)
        if score is None:
            continue
        available_scores.append(score)
        weight = len(details) if isinstance(details, dict) and details else len(records)
        dataset_summaries.append(
            {
                "dataset": dataset,
                "score": score,
                "raw_accuracy": raw_accuracy,
                "total_count": weight,
                "case_count": len(dataset_cases),
                "failed_count": sum(
                    1 for case in dataset_cases if case["status"] == "failed"
                ),
            }
        )
        if weight:
            weighted_score += score * weight
            total_weight += weight

    if total_weight:
        total_score = round(weighted_score / total_weight, 4)
    elif available_scores:
        total_score = round(sum(available_scores) / len(available_scores), 4)
    else:
        total_score = None

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": prepared.tool,
        "subcommand": prepared.subcommand,
        "total_score": total_score,
        "score_source": (
            "ais_bench_results_json" if total_score is not None else None
        ),
        "case_count": len(cases),
        "summary": {"datasets": dataset_summaries} if dataset_summaries else {},
        "cases": cases,
        "source": {
            "results_dir": (
                str(model_results_dir)
                if model_results_dir is not None and model_results_dir.is_dir()
                else None
            ),
            "predictions_dir": (
                str(model_predictions_dir)
                if model_predictions_dir is not None
                and model_predictions_dir.is_dir()
                else None
            ),
            "result_jsons": [str(path) for path in result_paths],
            "prediction_files": [str(path) for path in prediction_paths],
            "failed_prediction_files": [str(path) for path in failed_paths],
        },
    }


def _build_dataset_cases(
    dataset: str,
    result_doc: dict | None,
    records: list[dict],
    failed_records: list[dict],
) -> list[dict]:
    details = (
        result_doc.get("details")
        if isinstance(result_doc, dict) and isinstance(result_doc.get("details"), dict)
        else {}
    )
    detail_items = sorted(details.items(), key=lambda item: _id_sort_key(item[0]))
    sorted_records = sorted(records, key=lambda record: _id_sort_key(record.get("id")))

    attempts: dict[str, int] = {}
    cases: list[dict] = []
    existing_ids: set[str] = set()

    for index, (detail_key, detail) in enumerate(detail_items):
        record = sorted_records[index] if index < len(sorted_records) else None
        case = _case_from_pair(dataset, str(detail_key), detail, record, attempts)
        cases.append(case)
        if record is not None and record.get("id") is not None:
            existing_ids.add(str(record.get("id")))

    for record in sorted_records[len(detail_items) :]:
        cases.append(_case_from_prediction(dataset, record, attempts))
        if record.get("id") is not None:
            existing_ids.add(str(record.get("id")))

    for record in failed_records:
        case_id = str(record.get("id"))
        if case_id in existing_ids:
            continue
        cases.append(_case_from_failed(dataset, record, attempts))
        existing_ids.add(case_id)

    return cases


def _case_from_pair(
    dataset: str,
    detail_key: str,
    detail: dict,
    record: dict | None,
    attempts: dict[str, int],
) -> dict:
    score = _detail_score(detail)
    if record is None:
        case_id = detail_key
        return _case(
            dataset=dataset,
            case_id=case_id,
            attempt=_next_attempt(attempts, case_id),
            status="other",
            score=score,
            started_at=None,
            finished_at=None,
            input_tokens=None,
            output_tokens=None,
            exception_type=None,
            exception_message=None,
        )

    case_id = str(record.get("id")) if record.get("id") is not None else detail_key
    started_at, finished_at, input_tokens, output_tokens = _record_metrics(record)
    return _case(
        dataset=dataset,
        case_id=case_id,
        attempt=_next_attempt(attempts, case_id),
        status=_record_status(record),
        score=score,
        started_at=started_at,
        finished_at=finished_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        exception_type=None,
        exception_message=None,
    )


def _case_from_prediction(
    dataset: str,
    record: dict,
    attempts: dict[str, int],
) -> dict:
    case_id = str(record.get("id")) if record.get("id") is not None else "unknown"
    started_at, finished_at, input_tokens, output_tokens = _record_metrics(record)
    return _case(
        dataset=dataset,
        case_id=case_id,
        attempt=_next_attempt(attempts, case_id),
        status=_record_status(record),
        score=None,
        started_at=started_at,
        finished_at=finished_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        exception_type=None,
        exception_message=None,
    )


def _case_from_failed(
    dataset: str,
    record: dict,
    attempts: dict[str, int],
) -> dict:
    case_id = str(record.get("id")) if record.get("id") is not None else "unknown"
    started_at, finished_at, input_tokens, output_tokens = _record_metrics(record)
    exception_type, exception_message = _error_info(record.get("error_info"))
    return _case(
        dataset=dataset,
        case_id=case_id,
        attempt=_next_attempt(attempts, case_id),
        status="failed",
        score=None,
        started_at=started_at,
        finished_at=finished_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        exception_type=exception_type,
        exception_message=exception_message,
    )


def _case(
    *,
    dataset: str,
    case_id: str,
    attempt: int,
    status: str,
    score: float | None,
    started_at: object,
    finished_at: object,
    input_tokens: int | None,
    output_tokens: int | None,
    exception_type: object,
    exception_message: object,
) -> dict:
    return {
        "case_id": case_id,
        "dataset": dataset,
        "attempt": attempt,
        "status": status,
        "score": score,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": _duration_sec(started_at, finished_at),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "exception_type": exception_type,
        "exception_message": exception_message,
    }


def _next_attempt(attempts: dict[str, int], case_id: str) -> int:
    attempt = attempts.get(case_id, 0) + 1
    attempts[case_id] = attempt
    return attempt


def _dataset_score(details: object, raw_accuracy: float | None) -> float | None:
    """优先用 details 的逐样本正确性计算 0-1 得分，避免百分制/小数口径歧义。"""
    if isinstance(details, dict):
        scores = []
        for detail in details.values():
            if isinstance(detail, dict):
                value = _detail_score(detail)
                if value is not None:
                    scores.append(value)
        if scores:
            return round(sum(scores) / len(scores), 4)
    if raw_accuracy is None:
        return None
    if raw_accuracy > 1.0:
        return round(raw_accuracy / 100.0, 4)
    return round(raw_accuracy, 4)


def _detail_score(detail: dict) -> float | None:
    correct = detail.get("correct")
    if isinstance(correct, bool):
        return 1.0 if correct else 0.0
    if isinstance(correct, list):
        if not correct:
            return 0.0
        return round(sum(1 for item in correct if item) / len(correct), 4)
    return None


def _record_status(record: dict) -> str:
    success = record.get("success")
    if success is True:
        return "success"
    if success is False:
        return "failed"
    return "other"


def _record_metrics(
    record: dict,
) -> tuple[object, object, int | None, int | None]:
    started_at = record.get("start_time") or record.get("started_at")
    finished_at = record.get("end_time") or record.get("finished_at")
    return (
        started_at,
        finished_at,
        _as_int(record.get("input_tokens")),
        _as_int(record.get("output_tokens")),
    )


def _error_info(value: object) -> tuple[object, object]:
    if isinstance(value, dict):
        return (
            value.get("exception_type") or value.get("type"),
            value.get("exception_message") or value.get("message"),
        )
    if isinstance(value, str) and value:
        return None, value
    return None, None


def _dataset_names(
    result_paths: list[Path],
    prediction_paths: list[Path],
    failed_paths: list[Path],
) -> list[str]:
    names = {path.stem for path in result_paths}
    for path in prediction_paths:
        if not _PARTIAL_SUFFIX.search(path.stem):
            names.add(path.stem)
    for path in failed_paths:
        name = path.name[: -len("_failed.jsonl")]
        if not _PARTIAL_SUFFIX.search(name):
            names.add(name)
    return sorted(names)


def _glob_json(directory: Path | None) -> list[Path]:
    if directory is None or not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def _glob_jsonl(directory: Path | None, *, failed: bool) -> list[Path]:
    if directory is None or not directory.is_dir():
        return []
    paths = []
    for path in directory.glob("*.jsonl"):
        if not path.is_file():
            continue
        is_failed = path.name.endswith("_failed.jsonl")
        if is_failed == failed:
            paths.append(path)
    return sorted(paths)


def _id_sort_key(value: object) -> tuple[int, object]:
    try:
        return 0, int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1, str(value)


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


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _read_json(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_jsonl(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    records = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except (OSError, UnicodeDecodeError):
        return records
    return records
