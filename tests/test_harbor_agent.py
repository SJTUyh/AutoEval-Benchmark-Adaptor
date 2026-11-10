import ast

import pytest

from autoeval_adaptor.errors import ConfigError
from autoeval_adaptor.registry import get_adapter

from fakes import make_fake_benchmark


def _params(tmp_path, benchmark, **extra):
    params = {
        "abbr": "terminus-2",
        "agent_name": "terminus-2",
        "model_names": ["hosted_vllm/qwen3"],
        "api_base": "http://127.0.0.1:8080/v1",
        "path": "/data/terminal-bench-2",
        "n_tasks": 10,
        "work_dir": str(tmp_path / "run"),
        "ais_bench_root": str(benchmark),
    }
    params.update(extra)
    return params


def test_prepare_renders_agent_config(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "harbor_agent")
    prepared = adapter.prepare(_params(tmp_path, benchmark))

    content = prepared.config_content
    ast.parse(content)
    assert "from ais_bench.benchmark.runners.harbor_runner import HarborRunner" in content
    assert "HarborAgentTask" in content
    assert "HarborSummarizer" in content
    assert "harbor_terminal-bench-2" in content
    assert "'yes': True" in content
    assert "'debug': False" in content

    assert "agent" in prepared.command
    mode_positions = [i for i, value in enumerate(prepared.command) if value == "-m"]
    assert prepared.command[mode_positions[-1] + 1] == "agent"


def test_debug_flows_into_cli_and_dataset_args(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "harbor_agent")
    prepared = adapter.prepare(_params(tmp_path, benchmark, debug=True))

    assert "--debug" in prepared.command
    assert "'debug': True" in prepared.config_content


def test_dataset_abbr_can_be_overridden(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "harbor_agent")
    prepared = adapter.prepare(
        _params(tmp_path, benchmark, dataset_abbr="harbor_custom")
    )
    assert "'abbr': 'harbor_custom'" in prepared.config_content


def test_advanced_harbor_fields_are_passed_through(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "harbor_agent")
    prepared = adapter.prepare(
        _params(
            tmp_path,
            benchmark,
            concurrency_group="group-a",
            install_only=True,
            metrics={"latency": True},
            artifacts=["ctrf.json"],
            extra_instruction_paths=["/data/extra.md"],
            environment_env={"HTTP_PROXY": "http://127.0.0.1:7890"},
            extra_docker_compose=["/data/overlay.yaml"],
            override_cpus=2.0,
            override_memory_mb=4096,
            override_storage_mb=8192,
            override_gpus=0,
            verifier_env=["A=B"],
            verifier_import_path="pkg.mod:Verifier",
            verifier_kwargs={"strict": True},
            registry_url="https://registry.example.com",
            registry_path="/data/registry",
        )
    )

    content = prepared.config_content
    ast.parse(content)
    for fragment in [
        "'concurrency_group': 'group-a'",
        "'install_only': True",
        "'metrics': {'latency': True}",
        "'artifacts': ['ctrf.json']",
        "'extra_instruction_paths': ['/data/extra.md']",
        "'environment_env': {'HTTP_PROXY': 'http://127.0.0.1:7890'}",
        "'extra_docker_compose': ['/data/overlay.yaml']",
        "'override_cpus': 2.0",
        "'override_memory_mb': 4096",
        "'override_storage_mb': 8192",
        "'override_gpus': 0",
        "'verifier_env': ['A=B']",
        "'verifier_import_path': 'pkg.mod:Verifier'",
        "'verifier_kwargs': {'strict': True}",
        "'registry_url': 'https://registry.example.com'",
        "'registry_path': '/data/registry'",
    ]:
        assert fragment in content


def test_reuse_and_purge_flags(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "harbor_agent")
    prepared = adapter.prepare(
        _params(
            tmp_path,
            benchmark,
            reuse="20260101_000000",
            purge_exception_cases=True,
        )
    )

    command = prepared.command
    assert command[command.index("--reuse") + 1] == "20260101_000000"
    assert "--purge-exception-cases" in command


def test_requires_agent_and_dataset(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "harbor_agent")

    base = _params(tmp_path, benchmark)
    without_agent = dict(base)
    without_agent.pop("agent_name")
    with pytest.raises(ConfigError, match="agent_name 或 agent_import_path"):
        adapter.prepare(without_agent)

    without_dataset = dict(base)
    without_dataset.pop("path")
    with pytest.raises(ConfigError, match="path 或 dataset_name_version"):
        adapter.prepare(without_dataset)
