import ast

import pytest

from autoeval_adaptor.errors import ConfigError
from autoeval_adaptor.registry import get_adapter

from fakes import make_fake_benchmark


def _params(tmp_path, benchmark, **extra):
    params = {
        "abbr": "acc-run",
        "model_type": "vllm_api_general_chat",
        "datasets": ["gpqa_gen_0_shot_cot_chat_prompt"],
        "host_port": 8080,
        "generation_kwargs": {"temperature": 0.6},
        "dataset_test_range": "[0:10]",
        "max_num_workers": 2,
        "work_dir": str(tmp_path / "run"),
        "ais_bench_root": str(benchmark),
    }
    params.update(extra)
    return params


def test_prepare_renders_config_and_command(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "llm_accuracy")
    prepared = adapter.prepare(_params(tmp_path, benchmark))

    content = prepared.config_content
    ast.parse(content)
    assert "vllm_api_general_chat import models as _base_models" in content
    assert "gpqa_gen_0_shot_cot_chat_prompt import gpqa_datasets as _dataset_0" in content
    assert "models[0].update(" in content
    assert "'abbr': 'acc-run'" in content
    assert "'host_port': 8080" in content
    assert "test_range" in content and "'[0:10]'" in content
    assert "work_dir = " in content

    assert prepared.command[1:3] == ["-m", "ais_bench.benchmark.cli.main"]
    assert "all" in prepared.command
    workers_index = prepared.command.index("--max-num-workers")
    assert prepared.command[workers_index + 1] == "2"
    assert prepared.cwd == benchmark.resolve()
    assert prepared.config_path.parent.name == ".adaptor"


def test_prepare_rejects_unknown_param(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "llm_accuracy")

    with pytest.raises(ConfigError, match="未知参数: host_ipx"):
        adapter.prepare(_params(tmp_path, benchmark, host_ipx="127.0.0.1"))


def test_prepare_requires_datasets(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "llm_accuracy")

    with pytest.raises(ConfigError, match="params.datasets 不能为空"):
        adapter.prepare(_params(tmp_path, benchmark, datasets=[]))


def test_prepare_accepts_model_overrides(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "llm_accuracy")
    prepared = adapter.prepare(
        _params(tmp_path, benchmark, model_overrides={"stream": True})
    )
    assert "'stream': True" in prepared.config_content


def test_prepare_maps_accuracy_cli_flags(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "llm_accuracy")
    prepared = adapter.prepare(
        _params(
            tmp_path,
            benchmark,
            num_prompts=50,
            max_workers_per_gpu=2,
            dump_eval_details=True,
            dump_extract_rate=True,
            merge_ds=True,
        )
    )

    command = prepared.command
    assert command[command.index("--num-prompts") + 1] == "50"
    assert command[command.index("--max-workers-per-gpu") + 1] == "2"
    assert "--dump-eval-details" in command
    assert "--dump-extract-rate" in command
    assert "--merge-ds" in command


def test_prepare_rejects_invalid_num_prompts(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    adapter = get_adapter("ais_bench", "llm_accuracy")

    with pytest.raises(ConfigError, match="num_prompts 必须"):
        adapter.prepare(_params(tmp_path, benchmark, num_prompts=0))
