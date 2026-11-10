import pytest

from autoeval_adaptor.discovery import resolve_dataset, resolve_model, resolve_root
from autoeval_adaptor.errors import DiscoveryError

from fakes import make_fake_benchmark


def test_resolve_root_and_configs(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    root = resolve_root(benchmark)
    assert root == benchmark.resolve()

    model = resolve_model(root, "vllm_api_general_chat")
    assert model.module == (
        "ais_bench.benchmark.configs.models.vllm_api.vllm_api_general_chat"
    )
    assert model.variable == "models"

    dataset = resolve_dataset(root, "gpqa_gen_0_shot_cot_chat_prompt")
    assert dataset.module.endswith("datasets.gpqa.gpqa_gen_0_shot_cot_chat_prompt")
    assert dataset.variable == "gpqa_datasets"


def test_invalid_root_rejected(tmp_path):
    with pytest.raises(DiscoveryError, match="AISBench 根目录不合法"):
        resolve_root(tmp_path)


def test_ambiguous_dataset_alias_rejected(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    collections = (
        benchmark / "ais_bench" / "benchmark" / "configs" / "dataset_collections"
    )
    collections.mkdir(parents=True)
    (collections / "gpqa_gen_0_shot_cot_chat_prompt.py").write_text(
        "other_datasets = []\n", encoding="utf-8"
    )

    with pytest.raises(DiscoveryError, match="命中多个文件"):
        resolve_dataset(benchmark, "gpqa_gen_0_shot_cot_chat_prompt")


def test_non_service_model_rejected(tmp_path):
    benchmark = make_fake_benchmark(tmp_path)
    hf_dir = benchmark / "ais_bench" / "benchmark" / "configs" / "models" / "hf_models"
    hf_dir.mkdir(parents=True)
    (hf_dir / "hf_base.py").write_text("models = []\n", encoding="utf-8")

    with pytest.raises(DiscoveryError, match="仅支持服务化模型配置"):
        resolve_model(benchmark, "hf_base")
