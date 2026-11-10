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
