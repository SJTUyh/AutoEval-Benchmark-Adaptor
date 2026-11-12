import json
from types import SimpleNamespace

from autoeval_adaptor.tools.ais_bench.llm_results import collect_llm_results


def _prepared(abbr="m"):
    return SimpleNamespace(
        tool="ais_bench",
        subcommand="llm_accuracy",
        summary={"abbr": abbr},
    )


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def _detail(prompt, correct):
    return {"prompt": [{"role": "HUMAN", "prompt": prompt}], "correct": [correct]}


def _prediction(
    case_id,
    prompt,
    *,
    success=True,
    start="2026-11-11T10:00:00+00:00",
    end="2026-11-11T10:00:10+00:00",
    input_tokens=100,
    output_tokens=10,
):
    return {
        "data_abbr": "d",
        "id": case_id,
        "success": success,
        "origin_prompt": [{"role": "HUMAN", "prompt": prompt}],
        "start_time": start,
        "end_time": end,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def test_aligns_details_and_predictions_by_id(tmp_path):
    _write_json(
        tmp_path / "results" / "m" / "d.json",
        {
            "accuracy": 50.0,
            "type": "GEN",
            "details": {"0": _detail("q0", True), "1": _detail("q1", False)},
        },
    )
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d.jsonl",
        [
            _prediction(
                1,
                "q1",
                start="2026-11-11T10:00:05+00:00",
                end="2026-11-11T10:00:15+00:00",
                input_tokens=200,
                output_tokens=20,
            ),
            _prediction(0, "q0"),
        ],
    )

    payload = collect_llm_results(_prepared(), tmp_path)

    assert payload["total_score"] == 0.5
    assert payload["score_source"] == "ais_bench_results_json"
    assert payload["case_count"] == 2
    assert payload["summary"]["datasets"] == [
        {
            "dataset": "d",
            "score": 0.5,
            "raw_accuracy": 50.0,
            "total_count": 2,
            "case_count": 2,
            "failed_count": 0,
        }
    ]

    first, second = payload["cases"]
    assert first["case_id"] == "0"
    assert first["dataset"] == "d"
    assert first["attempt"] == 1
    assert first["status"] == "success"
    assert first["score"] == 1.0
    assert first["duration_sec"] == 10.0
    assert first["input_tokens"] == 100
    assert first["output_tokens"] == 10

    assert second["case_id"] == "1"
    assert second["score"] == 0.0
    assert second["input_tokens"] == 200
    assert second["output_tokens"] == 20


def test_status_is_independent_of_score(tmp_path):
    _write_json(
        tmp_path / "results" / "m" / "d.json",
        {"accuracy": 100.0, "details": {"0": _detail("q0", True)}},
    )
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d.jsonl",
        [_prediction(0, "q0", success=False)],
    )

    case = collect_llm_results(_prepared(), tmp_path)["cases"][0]

    assert case["score"] == 1.0
    assert case["status"] == "failed"


def test_includes_failed_predictions(tmp_path):
    _write_json(
        tmp_path / "results" / "m" / "d.json",
        {
            "accuracy": 50.0,
            "details": {"0": _detail("q0", True), "1": _detail("q1", False)},
        },
    )
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d.jsonl",
        [_prediction(0, "q0"), _prediction(1, "q1")],
    )
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d_failed.jsonl",
        [
            {
                "data_abbr": "d",
                "id": 2,
                "input": "q2",
                "error_info": "connection reset",
                "start_time": "2026-11-11T10:01:00+00:00",
                "end_time": "2026-11-11T10:01:02+00:00",
                "input_tokens": None,
                "output_tokens": None,
            }
        ],
    )

    payload = collect_llm_results(_prepared(), tmp_path)

    assert payload["case_count"] == 3
    failed = payload["cases"][2]
    assert failed["case_id"] == "2"
    assert failed["status"] == "failed"
    assert failed["score"] is None
    assert failed["duration_sec"] == 2.0
    assert failed["input_tokens"] is None
    assert failed["exception_message"] == "connection reset"
    assert payload["summary"]["datasets"][0]["failed_count"] == 1


def test_multiple_datasets_weighted_score(tmp_path):
    _write_json(
        tmp_path / "results" / "m" / "d1.json",
        {
            "accuracy": 50.0,
            "details": {"0": _detail("a0", True), "1": _detail("a1", False)},
        },
    )
    _write_json(
        tmp_path / "results" / "m" / "d2.json",
        {"accuracy": 100.0, "details": {"0": _detail("b0", True)}},
    )
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d1.jsonl",
        [_prediction(0, "a0"), _prediction(1, "a1")],
    )
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d2.jsonl",
        [_prediction(0, "b0")],
    )

    payload = collect_llm_results(_prepared(), tmp_path)

    assert payload["case_count"] == 3
    assert payload["total_score"] == 0.6667
    assert [item["dataset"] for item in payload["summary"]["datasets"]] == ["d1", "d2"]


def test_predictions_without_results_json(tmp_path):
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d.jsonl",
        [_prediction(0, "q0")],
    )

    payload = collect_llm_results(_prepared(), tmp_path)

    assert payload["total_score"] is None
    assert payload["score_source"] is None
    assert payload["case_count"] == 1
    assert payload["cases"][0]["score"] is None
    assert payload["summary"] == {}


def test_corrupt_files_and_missing_dir(tmp_path):
    result_path = tmp_path / "results" / "m" / "d.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text("{broken", encoding="utf-8")
    prediction_path = tmp_path / "predictions" / "m" / "d.jsonl"
    prediction_path.parent.mkdir(parents=True)
    prediction_path.write_text(
        "{broken\n" + json.dumps(_prediction(0, "q0")) + "\n",
        encoding="utf-8",
    )

    payload = collect_llm_results(_prepared(), tmp_path)
    assert payload["total_score"] is None
    assert payload["case_count"] == 1
    assert payload["cases"][0]["score"] is None

    empty = collect_llm_results(_prepared(), tmp_path / "missing")
    assert empty["total_score"] is None
    assert empty["case_count"] == 0
    assert empty["cases"] == []
    assert empty["summary"] == {}


def test_accuracy_fraction_fallback_without_details(tmp_path):
    _write_json(
        tmp_path / "results" / "m" / "d.json",
        {"accuracy": 0.75},
    )
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d.jsonl",
        [_prediction(0, "q0"), _prediction(1, "q1")],
    )

    payload = collect_llm_results(_prepared(), tmp_path)

    assert payload["total_score"] == 0.75
    assert payload["summary"]["datasets"][0]["score"] == 0.75
    assert payload["summary"]["datasets"][0]["raw_accuracy"] == 0.75


def test_detail_keys_sort_numerically(tmp_path):
    _write_json(
        tmp_path / "results" / "m" / "d.json",
        {
            "accuracy": 50.0,
            "details": {"10": _detail("q10", False), "2": _detail("q2", True)},
        },
    )
    _write_jsonl(
        tmp_path / "predictions" / "m" / "d.jsonl",
        [_prediction(2, "q2"), _prediction(10, "q10")],
    )

    cases = collect_llm_results(_prepared(), tmp_path)["cases"]

    assert [(case["case_id"], case["score"]) for case in cases] == [
        ("2", 1.0),
        ("10", 0.0),
    ]
