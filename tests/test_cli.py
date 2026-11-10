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
