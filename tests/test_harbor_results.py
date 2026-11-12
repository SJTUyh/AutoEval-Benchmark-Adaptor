from pathlib import Path
from types import SimpleNamespace

from autoeval_adaptor.tools.ais_bench.harbor_results import collect_harbor_results


def _prepared(abbr="m", dataset_abbr="d"):
    return SimpleNamespace(
        tool="ais_bench",
        subcommand="harbor_agent",
        summary={"abbr": abbr, "dataset_abbr": dataset_abbr},
    )


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    path.write_text(json.dumps(data), encoding="utf-8")


def _make_summary(output_dir, data):
    _write_json(output_dir / "results" / "m" / "d.json", data)


def _make_trial(output_dir, name, data):
    _write_json(
        output_dir / "results" / "m" / "d" / "details" / name / "result.json",
        data,
    )


def test_collects_summary_and_cases(tmp_path):
    _make_summary(
        tmp_path,
        {
            "total_count": 3,
            "n_errors": 1,
            "n_total_trials": 3,
            "avg_score": 0.5,
            "pass_at_k": {"pass@1": 0.5},
            "reward_distribution": [{"score": 1.0, "count": 1}],
            "exception_distribution": [
                {"exception_type": "AgentTimeoutError", "count": 1}
            ],
        },
    )
    _make_trial(
        tmp_path,
        "trial_00000",
        {
            "trial_name": "taskA__t1",
            "task_name": "taskA",
            "started_at": "2026-11-11T10:00:00+08:00",
            "finished_at": "2026-11-11T10:00:10+08:00",
            "verifier_result": {"rewards": {"reward": 1.0}},
            "agent_result": {"n_input_tokens": 100, "n_output_tokens": 10},
            "step_results": [
                {"agent_result": {"n_input_tokens": 50, "n_output_tokens": 5}}
            ],
        },
    )
    _make_trial(
        tmp_path,
        "trial_00001",
        {
            "trial_name": "taskB__t1",
            "task_name": "taskB",
            "started_at": "2026-11-11T10:01:00+08:00",
            "finished_at": "2026-11-11T10:01:30+08:00",
            "exception_info": {
                "exception_type": "AgentTimeoutError",
                "exception_message": "timed out",
            },
        },
    )
    _make_trial(
        tmp_path,
        "trial_00002",
        {
            "trial_name": "taskC__t1",
            "task_name": "taskC",
            "started_at": "bad",
            "finished_at": "worse",
            "verifier_result": {"rewards": {"reward": 0.0}},
        },
    )

    payload = collect_harbor_results(_prepared(), tmp_path)

    assert payload["schema_version"] == 1
    assert payload["total_score"] == 0.5
    assert payload["score_source"] == "ais_bench_results_json"
    assert payload["case_count"] == 3
    assert payload["summary"]["n_errors"] == 1
    assert payload["summary"]["pass_at_k"] == {"pass@1": 0.5}
    assert Path(payload["source"]["result_json"]) == (
        tmp_path / "results" / "m" / "d.json"
    )
    assert Path(payload["source"]["job_dir"]) == (
        tmp_path / "results" / "m" / "d" / "details"
    )

    first, second, third = payload["cases"]
    assert first["case_id"] == "taskA"
    assert first["status"] == "success"
    assert first["score"] == 1.0
    assert first["duration_sec"] == 10.0
    assert first["input_tokens"] == 150
    assert first["output_tokens"] == 15
    assert first["attempt"] == 1

    assert second["case_id"] == "taskB"
    assert second["status"] == "failed"
    assert second["score"] is None
    assert second["input_tokens"] is None
    assert second["output_tokens"] is None
    assert second["exception_type"] == "AgentTimeoutError"
    assert second["exception_message"] == "timed out"

    # 状态与得分无关：0 分且无异常仍是 success；时间非法时 duration 为 None
    assert third["status"] == "success"
    assert third["score"] == 0.0
    assert third["duration_sec"] is None


def test_legacy_task_dir_layout(tmp_path):
    _make_summary(tmp_path, {"avg_score": 0.5, "total_count": 1})
    _make_trial(
        tmp_path,
        "taskA__suffix",
        {
            "trial_name": "taskA__suffix",
            "task_name": "taskA",
            "started_at": "2026-11-11T10:00:00Z",
            "finished_at": "2026-11-11T10:00:10Z",
            "verifier_result": {"rewards": {"reward": 1.0}},
            "agent_result": {"n_input_tokens": 10, "n_output_tokens": 2},
        },
    )

    payload = collect_harbor_results(_prepared(), tmp_path)

    assert payload["case_count"] == 1
    case = payload["cases"][0]
    assert case["case_id"] == "taskA"
    assert case["trial_dir"] == "taskA__suffix"
    assert case["status"] == "success"
    assert case["duration_sec"] == 10.0
    assert case["input_tokens"] == 10
    assert case["output_tokens"] == 2


def test_attempts_increment_for_same_case(tmp_path):
    _make_trial(
        tmp_path,
        "trial_00000",
        {"task_name": "taskA", "verifier_result": {"rewards": {"reward": 1.0}}},
    )
    _make_trial(
        tmp_path,
        "trial_00001",
        {"task_name": "taskA", "verifier_result": {"rewards": {"reward": 0.0}}},
    )

    payload = collect_harbor_results(_prepared(), tmp_path)

    assert [case["attempt"] for case in payload["cases"]] == [1, 2]
    assert payload["total_score"] == 0.5
    assert payload["score_source"] == "trials"


def test_cancelled_is_other(tmp_path):
    _make_trial(
        tmp_path,
        "trial_00000",
        {
            "task_name": "taskA",
            "exception_info": {"exception_type": "CancelledError"},
        },
    )

    case = collect_harbor_results(_prepared(), tmp_path)["cases"][0]

    assert case["status"] == "other"
    assert case["score"] is None


def test_fallback_score_and_empty_cases(tmp_path):
    _make_trial(
        tmp_path,
        "trial_00000",
        {"task_name": "taskA", "verifier_result": {"rewards": {"reward": 1.0}}},
    )
    _make_trial(tmp_path, "trial_00001", {"task_name": "taskB"})

    payload = collect_harbor_results(_prepared(), tmp_path)
    assert payload["total_score"] == 0.5
    assert payload["score_source"] == "trials"

    empty = collect_harbor_results(_prepared(), tmp_path / "missing")
    assert empty["total_score"] is None
    assert empty["score_source"] is None
    assert empty["case_count"] == 0
    assert empty["cases"] == []
    assert empty["source"] == {"result_json": None, "job_dir": None}


def test_corrupt_files_do_not_raise(tmp_path):
    summary_path = tmp_path / "results" / "m" / "d.json"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text("{broken", encoding="utf-8")
    trial_dir = tmp_path / "results" / "m" / "d" / "details" / "trial_00000"
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text("{broken", encoding="utf-8")

    payload = collect_harbor_results(_prepared(), tmp_path)

    assert payload["total_score"] == 0.0
    assert payload["score_source"] == "trials"
    case = payload["cases"][0]
    assert case["status"] == "other"
    assert case["score"] is None
    assert case["started_at"] is None
