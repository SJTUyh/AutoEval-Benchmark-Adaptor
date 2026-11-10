from pathlib import Path


def make_fake_benchmark(root: Path) -> Path:
    """构造最小 AISBench 目录，仅用于解析与渲染测试。"""
    benchmark = root / "benchmark"
    cli_dir = benchmark / "ais_bench" / "benchmark" / "cli"
    cli_dir.mkdir(parents=True)
    (cli_dir / "main.py").write_text("", encoding="utf-8")

    models = (
        benchmark / "ais_bench" / "benchmark" / "configs" / "models" / "vllm_api"
    )
    models.mkdir(parents=True)
    (models / "vllm_api_general_chat.py").write_text(
        "models = [dict(abbr='base')]\n", encoding="utf-8"
    )

    datasets = (
        benchmark
        / "ais_bench"
        / "benchmark"
        / "configs"
        / "datasets"
        / "gpqa"
    )
    datasets.mkdir(parents=True)
    (datasets / "gpqa_gen_0_shot_cot_chat_prompt.py").write_text(
        "gpqa_datasets = []\n", encoding="utf-8"
    )
    return benchmark
