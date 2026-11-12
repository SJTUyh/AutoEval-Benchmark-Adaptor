import json

from autoeval_adaptor.cli import main
from autoeval_adaptor.tools.ais_bench import preflight as ais_preflight

from fakes import make_fake_benchmark


def _ok_probe(options=None):
    return {
        "python": {"version": "3.12.0", "executable": "/usr/bin/python3"},
        "ais_bench": {"ok": True, "version": "3.1.0", "file": "/opt/ais_bench.py"},
        "mmengine": {"ok": True, "version": "0.10.0"},
        "modules": [
            {"module": "m1", "ok": True, "missing": [], "error": None},
            {"module": "m2", "ok": True, "missing": [], "error": None},
            {"module": "m3", "ok": True, "missing": [], "error": None},
            {"module": "m4", "ok": True, "missing": [], "error": None},
        ],
        "datasets": [
            {
                "module": "d1",
                "ok": True,
                "variables": ["demo_datasets"],
                "error": None,
                "file": "/opt/d1.py",
            }
        ],
        "cli": {
            "ok": True,
            "options": options
            or ["-m", "--work-dir", "--max-num-workers", "--debug"],
            "mode_choices": ["all", "agent"],
            "error": None,
        },
        "model": {
            "ok": True,
            "class": "DemoModel",
            "init_params": [],
            "var_kwargs": True,
            "config_errors": {},
            "error": None,
        },
        "harbor": None,
    }


def _write_config(tmp_path, benchmark, tool="ais_bench", subcommand="llm_accuracy"):
    config = {
        "tool": tool,
        "subcommand": subcommand,
        "params": {
            "abbr": "cli-run",
            "model_type": "vllm_api_general_chat",
            "datasets": ["gpqa_gen_0_shot_cot_chat_prompt"],
            "work_dir": str(tmp_path / "run"),
            "ais_bench_root": str(benchmark),
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_dry_run_prints_config_and_command(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)

    assert main(["--config", str(config_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "生成的 AISBench 配置文件" in out
    assert "models[0].update(" in out
    assert "ais_bench.benchmark.cli.main" in out
    assert not (tmp_path / "run").exists()


def test_unknown_tool_returns_config_error(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark, tool="unknown")

    assert main(["--config", str(config_path), "--dry-run"]) == 2
    assert "不支持的 tool/subcommand" in capsys.readouterr().err


def test_cli_overrides_work_dir(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)
    override = tmp_path / "override"

    assert main(
        ["--config", str(config_path), "--dry-run", "--work-dir", str(override)]
    ) == 0
    assert str(override) in capsys.readouterr().out


def test_preflight_only_prints_report(tmp_path, capsys, monkeypatch):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)
    monkeypatch.setattr(
        ais_preflight, "run_probe", lambda prepared, spec, **kwargs: _ok_probe()
    )

    assert main(["--config", str(config_path), "--preflight-only"]) == 0
    out = capsys.readouterr().out
    assert "[preflight] ais_bench/llm_accuracy" in out
    assert "[PASS] AISBench 导入" in out


def test_preflight_errors_block_execution(tmp_path, capsys, monkeypatch):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)
    monkeypatch.setattr(
        ais_preflight,
        "run_probe",
        lambda prepared, spec, **kwargs: _ok_probe(options=["-m", "--work-dir"]),
    )

    assert main(["--config", str(config_path)]) == 2
    captured = capsys.readouterr()
    assert "--max-num-workers" in captured.out
    assert "预检未通过" in captured.err
    assert not (tmp_path / "run").exists()


def test_preflight_probe_failure_blocks_execution(tmp_path, capsys, monkeypatch):
    from autoeval_adaptor.preflight import ProbeError

    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)

    def boom(prepared, spec, **kwargs):
        raise ProbeError("目标解释器不可用")

    monkeypatch.setattr(ais_preflight, "run_probe", boom)

    assert main(["--config", str(config_path), "--preflight-only"]) == 2
    assert "目标解释器不可用" in capsys.readouterr().out


def _write_harbor_config(tmp_path, benchmark):
    config = {
        "tool": "ais_bench",
        "subcommand": "harbor_agent",
        "params": {
            "abbr": "cli-run",
            "agent_name": "terminus-2",
            "model_names": ["hosted_vllm/demo"],
            "path": "/data/demo-tasks",
            "dataset_abbr": "harbor_demo",
            "work_dir": str(tmp_path / "run"),
            "ais_bench_root": str(benchmark),
        },
    }
    path = tmp_path / "harbor.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _make_harbor_results(work_dir):
    result_dir = work_dir / "20260101_000000"
    model_dir = result_dir / "results" / "cli-run"
    model_dir.mkdir(parents=True)
    (model_dir / "harbor_demo.json").write_text(
        json.dumps({"avg_score": 1.0, "n_errors": 0, "total_count": 1}),
        encoding="utf-8",
    )
    trial_dir = model_dir / "harbor_demo" / "details" / "trial_00000"
    trial_dir.mkdir(parents=True)
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "task_name": "taskA",
                "trial_name": "taskA__t1",
                "started_at": "2026-11-11T10:00:00+08:00",
                "finished_at": "2026-11-11T10:00:10+08:00",
                "verifier_result": {"rewards": {"reward": 1.0}},
                "agent_result": {"n_input_tokens": 10, "n_output_tokens": 2},
            }
        ),
        encoding="utf-8",
    )
    return result_dir


def test_collect_results_writes_unified_file(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_harbor_config(tmp_path, benchmark)
    work_dir = tmp_path / "run"
    work_dir.mkdir()
    (work_dir / "adaptor_result.json").write_text(
        json.dumps({"status": "success"}), encoding="utf-8"
    )
    result_dir = _make_harbor_results(work_dir)

    assert (
        main(
            [
                "--config",
                str(config_path),
                "--collect-results",
                "--result-dir",
                str(result_dir),
            ]
        )
        == 0
    )

    out = capsys.readouterr().out
    assert "[collect]" in out
    assert "total_score=1.0" in out

    unified = json.loads(
        (work_dir / "unified_result.json").read_text(encoding="utf-8")
    )
    assert unified["total_score"] == 1.0
    assert unified["case_count"] == 1
    assert unified["cases"][0]["status"] == "success"
    assert unified["cases"][0]["duration_sec"] == 10.0
    assert unified["cases"][0]["input_tokens"] == 10
    assert unified["cases"][0]["output_tokens"] == 2

    record = json.loads(
        (work_dir / "adaptor_result.json").read_text(encoding="utf-8")
    )
    assert record["unified_result"]["case_count"] == 1
    assert record["unified_result_path"].endswith("unified_result.json")


def test_collect_results_missing_artifacts_returns_config_error(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_harbor_config(tmp_path, benchmark)

    assert main(["--config", str(config_path), "--collect-results"]) == 2
    assert "未找到可解析的测评产物目录" in capsys.readouterr().err


def test_collect_results_for_llm_accuracy(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)
    result_dir = tmp_path / "run" / "20260101_000000"
    result_path = result_dir / "results" / "cli-run" / "GPQA_diamond.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "accuracy": 100.0,
                "details": {
                    "0": {
                        "prompt": [{"role": "HUMAN", "prompt": "q"}],
                        "correct": [True],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    prediction_path = result_dir / "predictions" / "cli-run" / "GPQA_diamond.jsonl"
    prediction_path.parent.mkdir(parents=True)
    prediction_path.write_text(
        json.dumps(
            {
                "id": 0,
                "success": True,
                "start_time": "2026-11-11T10:00:00+00:00",
                "end_time": "2026-11-11T10:00:10+00:00",
                "input_tokens": 10,
                "output_tokens": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["--config", str(config_path), "--collect-results"]) == 0

    assert "total_score=1.0" in capsys.readouterr().out
    unified = json.loads(
        (tmp_path / "run" / "unified_result.json").read_text(encoding="utf-8")
    )
    assert unified["subcommand"] == "llm_accuracy"
    assert unified["total_score"] == 1.0
    assert unified["case_count"] == 1
    assert unified["cases"][0]["case_id"] == "0"
    assert unified["cases"][0]["status"] == "success"


def test_result_dir_requires_collect_results(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)

    assert (
        main(["--config", str(config_path), "--result-dir", str(tmp_path)]) == 2
    )
    assert "--result-dir" in capsys.readouterr().err


def test_normal_run_passes_adapter_to_execute(tmp_path, monkeypatch):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)
    captured = {}

    def fake_execute(prepared, *, dry_run=False, preflight=None, adapter=None):
        captured["adapter"] = adapter
        return {"status": "success", "exit_code": 0}

    monkeypatch.setattr("autoeval_adaptor.cli.execute", fake_execute)

    assert main(["--config", str(config_path), "--skip-preflight"]) == 0
    assert captured["adapter"] is not None
