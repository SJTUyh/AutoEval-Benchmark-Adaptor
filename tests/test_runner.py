import json
import os
import sys

from autoeval_adaptor.runner import execute
from autoeval_adaptor.tools.base import PreparedRun


def _prepare(tmp_path, command, *, timeout=None, config_content="# demo\n"):
    work_dir = tmp_path / "run"
    return PreparedRun(
        tool="demo",
        subcommand="demo",
        work_dir=work_dir,
        cwd=tmp_path,
        env=os.environ.copy(),
        command=command,
        config_path=work_dir / ".adaptor" / "demo.py",
        config_content=config_content,
        timeout=timeout,
        summary={"abbr": "demo"},
    )


def test_success_writes_log_result_and_detects_output_dir(tmp_path):
    run_dir = tmp_path / "run"
    script = (
        "import pathlib;"
        f"(pathlib.Path(r'{run_dir}') / '20260101_000000').mkdir(parents=True, exist_ok=True);"
        "print('hello adaptor')"
    )
    prepared = _prepare(tmp_path, [sys.executable, "-c", script])

    result = execute(prepared)

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert result["ais_bench_work_dir"].endswith("20260101_000000")
    assert (run_dir / "adaptor.log").read_text(encoding="utf-8").find("hello adaptor") >= 0
    assert prepared.config_path.read_text(encoding="utf-8") == "# demo\n"

    saved = json.loads((run_dir / "adaptor_result.json").read_text(encoding="utf-8"))
    assert saved["status"] == "success"
    assert saved["params"] == {"abbr": "demo"}


def test_non_zero_exit_is_failed(tmp_path):
    prepared = _prepare(tmp_path, [sys.executable, "-c", "import sys; sys.exit(3)"])
    result = execute(prepared)

    assert result["status"] == "failed"
    assert result["exit_code"] == 3
    assert (tmp_path / "run" / "adaptor_result.json").is_file()


def test_timeout_is_reported(tmp_path):
    prepared = _prepare(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=0.5,
    )
    result = execute(prepared)

    assert result["status"] == "timeout"
    assert result["exit_code"] == 124


def test_reuse_falls_back_to_existing_timestamp_dir(tmp_path):
    run_dir = tmp_path / "run"
    existing = run_dir / "20260101_000000"
    existing.mkdir(parents=True)

    prepared = _prepare(tmp_path, [sys.executable, "-c", "print('reused')"])
    result = execute(prepared)

    assert result["status"] == "success"
    assert result["ais_bench_work_dir"] == str(existing)


def test_dry_run_has_no_side_effects(tmp_path, capsys):
    prepared = _prepare(tmp_path, [sys.executable, "-c", "print('should not run')"])
    result = execute(prepared, dry_run=True)

    assert result["status"] == "dry_run"
    assert "models = []" not in capsys.readouterr().out
    assert not (tmp_path / "run").exists()
    assert result["unified_result"] is None
    assert result["unified_result_path"] is None
    assert result["unified_result_error"] is None


class _FakeAdapter:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def collect_results(self, prepared, output_dir):
        self.calls.append(output_dir)
        if self.error is not None:
            raise self.error
        return self.payload


def _success_script(run_dir):
    return (
        "import pathlib;"
        f"(pathlib.Path(r'{run_dir}') / '20260101_000000').mkdir(parents=True, exist_ok=True);"
        "print('done')"
    )


def test_success_writes_unified_result(tmp_path):
    run_dir = tmp_path / "run"
    prepared = _prepare(tmp_path, [sys.executable, "-c", _success_script(run_dir)])
    payload = {"total_score": 1.0, "case_count": 0, "cases": []}
    adapter = _FakeAdapter(payload)

    result = execute(prepared, adapter=adapter)

    assert result["status"] == "success"
    assert adapter.calls == [run_dir / "20260101_000000"]
    assert result["unified_result"] == payload
    assert result["unified_result_error"] is None
    assert result["unified_result_path"] == str(run_dir / "unified_result.json")
    assert json.loads(
        (run_dir / "unified_result.json").read_text(encoding="utf-8")
    ) == payload

    saved = json.loads((run_dir / "adaptor_result.json").read_text(encoding="utf-8"))
    assert saved["unified_result"] == payload
    assert saved["unified_result_path"] == str(run_dir / "unified_result.json")


def test_unimplemented_collector_does_not_fail_run(tmp_path):
    run_dir = tmp_path / "run"
    prepared = _prepare(tmp_path, [sys.executable, "-c", _success_script(run_dir)])

    result = execute(prepared, adapter=_FakeAdapter(None))

    assert result["status"] == "success"
    assert result["unified_result"] is None
    assert "尚未实现统一结果提取" in result["unified_result_error"]
    assert not (run_dir / "unified_result.json").exists()


def test_collector_error_does_not_fail_run(tmp_path, capsys):
    run_dir = tmp_path / "run"
    prepared = _prepare(tmp_path, [sys.executable, "-c", _success_script(run_dir)])

    result = execute(prepared, adapter=_FakeAdapter(error=RuntimeError("boom")))

    assert result["status"] == "success"
    assert result["unified_result"] is None
    assert "RuntimeError: boom" in result["unified_result_error"]
    assert "统一结果提取失败" in capsys.readouterr().err
    assert not (run_dir / "unified_result.json").exists()
