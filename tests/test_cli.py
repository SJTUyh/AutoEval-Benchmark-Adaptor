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
            "execution": "local",
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


def _write_partial_llm_config(tmp_path, benchmark):
    config = {
        "tool": "ais_bench",
        "subcommand": "llm_accuracy",
        "params": {
            "model_type": "vllm_api_general_chat",
            "datasets": ["gpqa_gen_0_shot_cot_chat_prompt"],
            "max_out_len": 262144,
            "generation_kwargs": {"temperature": 1.0, "top_p": 0.95},
            "ais_bench_root": str(benchmark),
            "execution": "local",
        },
    }
    path = tmp_path / "partial_llm.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _write_partial_harbor_config(tmp_path, benchmark):
    config = {
        "tool": "ais_bench",
        "subcommand": "harbor_agent",
        "params": {
            "agent_name": "mini-swe-agent",
            "agent_kwargs": {"step_limit": 500},
            "path": "/data/deep-swe/tasks",
            "dataset_abbr": "harbor_deepswe-v1.1",
            "n_attempts": 8,
            "environment_type": "docker",
            "ais_bench_root": str(benchmark),
            "execution": "local",
        },
    }
    path = tmp_path / "partial_harbor.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_partial_llm_config_merges_template(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_partial_llm_config(tmp_path, benchmark)

    assert main(["--config", str(config_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "使用配置模板: builtin:ais_bench/llm_accuracy" in out
    assert "'abbr': 'model-under-test'" in out
    assert "'host_port': 8080" in out
    assert "'batch_size': 1" in out
    assert "work_dir = " in out


def test_partial_config_without_template_fails(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_partial_llm_config(tmp_path, benchmark)

    assert main(["--config", str(config_path), "--dry-run", "--no-template"]) == 2
    assert "缺少必填参数: abbr" in capsys.readouterr().err


def test_partial_harbor_config_merges_template(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_partial_harbor_config(tmp_path, benchmark)

    assert main(["--config", str(config_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "使用配置模板: builtin:ais_bench/harbor_agent" in out
    assert "'abbr': 'model-under-test'" in out
    assert "'model_names': ['hosted_vllm/model-under-test']" in out
    assert "'api_base': 'http://127.0.0.1:8080/v1'" in out
    assert "work_dir = " in out


def test_show_template(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_partial_harbor_config(tmp_path, benchmark)

    assert main(["--config", str(config_path), "--show-template"]) == 0
    out = capsys.readouterr().out
    assert '"subcommand": "harbor_agent"' in out
    assert '"abbr": "model-under-test"' in out
    assert '"model_names"' in out


def test_custom_template_override(tmp_path, capsys):
    from autoeval_adaptor.template import load_template

    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_partial_llm_config(tmp_path, benchmark)

    template = load_template("ais_bench", "llm_accuracy").params
    template["abbr"] = "custom-abbr"
    template_path = tmp_path / "custom_template.json"
    template_path.write_text(
        json.dumps(
            {
                "tool": "ais_bench",
                "subcommand": "llm_accuracy",
                "params": template,
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--config",
                str(config_path),
                "--dry-run",
                "--template",
                str(template_path),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "使用配置模板" in out
    assert str(template_path) in out
    assert "'abbr': 'custom-abbr'" in out


def test_env_status_prints_json(tmp_path, capsys, monkeypatch):
    import autoeval_adaptor.cli as cli

    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)
    monkeypatch.setattr(
        cli,
        "status",
        lambda spec, params, docker: {
            "container": spec.container_name,
            "container_state": "running",
        },
    )

    assert main(["--config", str(config_path), "--env-status"]) == 0
    assert '"container_state": "running"' in capsys.readouterr().out


def test_build_env_invokes_build(tmp_path, monkeypatch):
    import autoeval_adaptor.cli as cli

    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)
    calls = []
    monkeypatch.setattr(
        cli,
        "build_image",
        lambda spec, force=False: calls.append((spec.image, force)),
    )

    assert main(["--config", str(config_path), "--build-env", "--force-rebuild"]) == 0
    assert calls and calls[0][1] is True
    assert calls[0][0].startswith("autoeval/ais_bench-llm_accuracy:")


def test_start_and_stop_env_dispatch(tmp_path, monkeypatch):
    import autoeval_adaptor.cli as cli

    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)
    started = []
    stopped = []
    monkeypatch.setattr(
        cli, "ensure_container", lambda spec, params, docker: started.append(spec)
    )
    monkeypatch.setattr(cli, "stop_container", lambda spec: stopped.append(spec))

    assert main(["--config", str(config_path), "--start-env"]) == 0
    assert main(["--config", str(config_path), "--stop-env"]) == 0
    assert started and stopped
    assert started[0].container_name == stopped[0].container_name


def test_local_flag_keeps_execution_local(tmp_path, capsys):
    benchmark = make_fake_benchmark(tmp_path)
    config_path = _write_config(tmp_path, benchmark)

    assert main(["--config", str(config_path), "--dry-run", "--local"]) == 0
    out = capsys.readouterr().out
    assert "execution: local" in out
    assert "docker exec" not in out
