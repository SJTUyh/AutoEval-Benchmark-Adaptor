from types import SimpleNamespace

from autoeval_adaptor.docker_env import ContainerRun
from autoeval_adaptor.preflight import SENTINEL, PreflightReport, ProbeError, run_probe
from autoeval_adaptor.tools.ais_bench import preflight as ais_preflight
from autoeval_adaptor.tools.base import PreparedRun


def _prepared(mode="all", command=None):
    return SimpleNamespace(
        tool="ais_bench",
        subcommand="llm_accuracy",
        mode=mode,
        preflight_context={},
        command=command
        or [
            "python",
            "-m",
            "ais_bench.benchmark.cli.main",
            "cfg.py",
            "-m",
            mode,
            "--work-dir",
            "out",
            "--max-num-workers",
            "1",
        ],
    )


def _probe(**overrides):
    probe = {
        "python": {"version": "3.12.0", "executable": "/usr/bin/python3"},
        "ais_bench": {"ok": True, "version": "3.1.0", "file": "/opt/ais_bench/__init__.py"},
        "mmengine": {"ok": True, "version": "0.10.0"},
        "modules": [
            {
                "module": "ais_bench.benchmark.tasks",
                "ok": True,
                "missing": [],
                "error": None,
            }
        ],
        "datasets": [
            {
                "module": "ais_bench.benchmark.configs.datasets.gpqa.gpqa_gen",
                "ok": True,
                "variables": ["gpqa_datasets"],
                "error": None,
                "file": "/opt/gpqa_gen.py",
            }
        ],
        "cli": {
            "ok": True,
            "options": ["-m", "--work-dir", "--max-num-workers", "--debug"],
            "mode_choices": ["all", "agent"],
            "error": None,
        },
        "model": {
            "ok": True,
            "class": "VLLMCustomAPIChat",
            "init_params": ["self", "path", "model", "stream"],
            "var_kwargs": False,
            "config_errors": {},
            "error": None,
        },
        "harbor": None,
    }
    probe.update(overrides)
    return probe


def _spec(**overrides):
    spec = {
        "modules": [
            {
                "module": "ais_bench.benchmark.tasks",
                "attrs": ["OpenICLApiInferTask"],
            }
        ],
        "datasets": [
            {
                "module": "ais_bench.benchmark.configs.datasets.gpqa.gpqa_gen",
                "variable": "gpqa_datasets",
            }
        ],
        "model": {
            "module": "ais_bench.benchmark.configs.models.vllm_api.vllm_api_general_chat",
            "variable": "models",
            "overrides": {"abbr": "x", "stream": True},
            "check_fields": ["abbr", "stream"],
        },
        "harbor": None,
    }
    spec.update(overrides)
    return spec


def _run_eval(prepared=None, spec=None, probe=None):
    report = PreflightReport(tool="ais_bench", subcommand="llm_accuracy")
    ais_preflight.evaluate_probe(
        report, prepared or _prepared(), spec or _spec(), probe or _probe()
    )
    return report


def test_all_checks_pass():
    report = _run_eval()
    assert report.ok
    assert report.errors == []
    assert report.environment["ais_bench"].startswith("3.1.0")


def test_missing_cli_flag_is_error():
    probe = _probe()
    probe["cli"]["options"] = ["-m", "--work-dir"]
    report = _run_eval(probe=probe)
    assert not report.ok
    assert any("--max-num-workers" in item.message for item in report.errors)


def test_unsupported_mode_is_error():
    probe = _probe()
    probe["cli"]["mode_choices"] = ["all"]
    report = _run_eval(prepared=_prepared(mode="agent"), probe=probe)
    assert not report.ok
    assert any("运行模式" == item.name for item in report.errors)


def test_model_unknown_field_is_error():
    spec = _spec()
    spec["model"]["check_fields"] = ["abbr", "unknown_field"]
    report = _run_eval(spec=spec)
    assert not report.ok
    assert any("unknown_field" in item.message for item in report.errors)


def test_model_config_validation_errors_are_reported():
    probe = _probe()
    probe["model"]["config_errors"] = {"host_port": "host_port must be a valid port"}
    report = _run_eval(probe=probe)
    assert not report.ok
    assert any("配置校验失败" in item.message for item in report.errors)


def test_missing_module_and_dataset_are_errors():
    probe = _probe()
    probe["modules"][0].update(ok=False, missing=["OpenICLApiInferTask"])
    probe["datasets"][0].update(ok=False, error="模块不存在")
    report = _run_eval(probe=probe)
    assert len(report.errors) >= 2


def test_harbor_agent_and_param_capability():
    spec = _spec(
        harbor={
            "agent_name": "terminus-2",
            "tokens": ["install_only", "new_param"],
        }
    )
    probe = _probe(
        harbor={
            "ok": True,
            "version": "0.21.0",
            "missing": [],
            "agent_ok": False,
            "agent_error": None,
            "source_missing": ["new_param"],
            "source_errors": [],
            "error": None,
        }
    )
    report = _run_eval(spec=spec, probe=probe)

    assert not report.ok
    assert any(item.name == "Harbor Agent" for item in report.errors)
    assert any("new_param" in item.message for item in report.warnings)


def test_probe_error_becomes_report_error(monkeypatch, tmp_path):
    def boom(prepared, spec, **kwargs):
        raise ProbeError("解释器不可用")

    monkeypatch.setattr(ais_preflight, "run_probe", boom)
    prepared = _prepared()
    prepared.tool = "ais_bench"
    prepared.subcommand = "llm_accuracy"

    report = ais_preflight.build_report(prepared)

    assert not report.ok
    assert "解释器不可用" in report.errors[0].message


def test_run_probe_wraps_docker_exec(monkeypatch, tmp_path):
    import json

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            stdout=SENTINEL + json.dumps({"stage": "base"}),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("autoeval_adaptor.preflight.subprocess.run", fake_run)
    prepared = PreparedRun(
        tool="ais_bench",
        subcommand="harbor_agent",
        work_dir=tmp_path,
        cwd="/benchmark",
        env={},
        command=["/opt/venvs/harbor/bin/python", "-m", "ais_bench.benchmark.cli.main"],
        python_executable="/opt/venvs/harbor/bin/python",
        container=ContainerRun(
            name="c1",
            image="img",
            isolation="socket",
            workdir="/benchmark",
            python="/opt/venvs/harbor/bin/python",
            env={"PYTHONUNBUFFERED": "1"},
        ),
    )

    result = run_probe(prepared, {}, stage="base")

    assert result["stage"] == "base"
    command = captured["command"]
    assert command[:5] == ["docker", "exec", "-i", "-w", "/benchmark"]
    assert command[-3] == "/opt/venvs/harbor/bin/python"
    assert command[-2] == "-c"
    assert captured["kwargs"]["cwd"] is None
