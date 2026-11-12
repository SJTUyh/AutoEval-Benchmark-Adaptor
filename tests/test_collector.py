import json
import os
from types import SimpleNamespace

import pytest

from autoeval_adaptor.collector import collect_results, resolve_result_dir
from autoeval_adaptor.errors import ConfigError


def _prepared(work_dir):
    return SimpleNamespace(
        tool="ais_bench",
        subcommand="harbor_agent",
        work_dir=work_dir,
    )


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class FakeAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def collect_results(self, prepared, output_dir):
        self.calls.append(output_dir)
        return self.payload


def test_resolve_explicit_dir(tmp_path):
    result_dir = tmp_path / "20260101_000000"
    (result_dir / "results").mkdir(parents=True)

    assert resolve_result_dir(tmp_path, result_dir) == result_dir


def _touch_dir(path, mtime):
    path.mkdir(exist_ok=True)
    os.utime(path, (mtime, mtime))


def test_resolve_explicit_parent_with_timestamp(tmp_path):
    older = tmp_path / "20260101_000000"
    newer = tmp_path / "20260102_000000"
    _touch_dir(older, 1000.0)
    _touch_dir(newer, 2000.0)

    assert resolve_result_dir(tmp_path, tmp_path) == newer


def test_resolve_from_adaptor_result(tmp_path):
    recorded = tmp_path / "recorded"
    recorded.mkdir()
    _write_json(
        tmp_path / "adaptor_result.json",
        {"ais_bench_work_dir": str(recorded)},
    )

    assert resolve_result_dir(tmp_path) == recorded


def test_resolve_latest_timestamp(tmp_path):
    older = tmp_path / "20260101_000000"
    newer = tmp_path / "20260102_000000"
    _touch_dir(older, 1000.0)
    _touch_dir(newer, 2000.0)

    assert resolve_result_dir(tmp_path) == newer


def test_resolve_missing_raises(tmp_path):
    with pytest.raises(ConfigError, match="未找到可解析的测评产物目录"):
        resolve_result_dir(tmp_path)

    with pytest.raises(ConfigError, match="不存在或不是目录"):
        resolve_result_dir(tmp_path, tmp_path / "missing")


def test_collect_writes_file_and_updates_record(tmp_path):
    result_dir = tmp_path / "20260101_000000"
    result_dir.mkdir()
    _write_json(tmp_path / "adaptor_result.json", {"status": "success"})
    payload = {"total_score": 0.5, "case_count": 1, "cases": []}
    adapter = FakeAdapter(payload)

    outcome = collect_results(_prepared(tmp_path), adapter, result_dir=result_dir)

    output = tmp_path / "unified_result.json"
    assert outcome["path"] == str(output)
    assert outcome["source_dir"] == str(result_dir)
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert adapter.calls == [result_dir]

    record = json.loads(
        (tmp_path / "adaptor_result.json").read_text(encoding="utf-8")
    )
    assert record["status"] == "success"
    assert record["unified_result"] == payload
    assert record["unified_result_path"] == str(output)
    assert record["unified_result_error"] is None


def test_collect_custom_output(tmp_path):
    result_dir = tmp_path / "20260101_000000"
    result_dir.mkdir()
    output = tmp_path / "custom" / "result.json"

    collect_results(
        _prepared(tmp_path),
        FakeAdapter({"case_count": 0}),
        result_dir=result_dir,
        output=output,
    )

    assert output.is_file()


def test_collect_unimplemented_raises(tmp_path):
    result_dir = tmp_path / "20260101_000000"
    result_dir.mkdir()

    with pytest.raises(ConfigError, match="尚未实现统一结果提取"):
        collect_results(_prepared(tmp_path), FakeAdapter(None), result_dir=result_dir)
    assert not (tmp_path / "unified_result.json").exists()


def test_collect_keeps_corrupt_record_untouched(tmp_path):
    result_dir = tmp_path / "20260101_000000"
    result_dir.mkdir()
    record_path = tmp_path / "adaptor_result.json"
    record_path.write_text("{broken", encoding="utf-8")

    collect_results(_prepared(tmp_path), FakeAdapter({"case_count": 0}), result_dir=result_dir)

    assert record_path.read_text(encoding="utf-8") == "{broken"
    assert (tmp_path / "unified_result.json").is_file()
