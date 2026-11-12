"""常规 LLM 精度测评适配器（AISBench mode=all）。"""

from __future__ import annotations

import copy
from pathlib import Path

from ...config import MISSING, ParamReader
from ...discovery import ConfigRef, resolve_dataset, resolve_model
from ...errors import ConfigError
from ...registry import register
from ..base import BaseAdapter, PreparedRun
from .common import (
    RuntimeOptions,
    as_python,
    build_command,
    build_env,
    make_config_path,
    take_runtime,
)

MODEL_FIELDS = {
    "path": "str",
    "model": "str",
    "stream": "bool",
    "request_rate": "float",
    "use_timestamp": "bool",
    "retry": "int",
    "api_key": "str",
    "host_ip": "str",
    "host_port": "int",
    "url": "str",
    "max_out_len": "int",
    "batch_size": "int",
    "trust_remote_code": "bool",
    "generation_kwargs": "dict",
}


@register("ais_bench", "llm_accuracy")
class AISBenchAccuracyAdapter(BaseAdapter):
    tool = "ais_bench"
    subcommand = "llm_accuracy"

    def prepare(self, params: dict) -> PreparedRun:
        reader = ParamReader(params)

        abbr = reader.take("abbr", required=True)
        model_type = reader.take("model_type", required=True)
        datasets = reader.take("datasets", kind="list_str", required=True)
        if not datasets:
            raise ConfigError("params.datasets 不能为空")
        test_range = reader.take("dataset_test_range")
        overrides = reader.take("model_overrides", kind="dict", default={})
        num_prompts = reader.take("num_prompts", kind="int")
        max_workers_per_gpu = reader.take("max_workers_per_gpu", kind="int", default=1)
        dump_eval_details = reader.take("dump_eval_details", kind="bool", default=False)
        dump_extract_rate = reader.take("dump_extract_rate", kind="bool", default=False)
        merge_ds = reader.take("merge_ds", kind="bool", default=False)
        runtime = take_runtime(reader)

        if num_prompts is not None and num_prompts < 1:
            raise ConfigError("params.num_prompts 必须是 >= 1 的整数")
        if max_workers_per_gpu < 1:
            raise ConfigError("params.max_workers_per_gpu 必须是 >= 1 的整数")

        model_fields: dict = {}
        for key, kind in MODEL_FIELDS.items():
            value = reader.take_optional(key, kind=kind)
            if value is not MISSING:
                model_fields[key] = value
        reader.finish()

        model_fields.update(copy.deepcopy(overrides))
        model_fields["abbr"] = abbr

        model_ref = resolve_model(runtime.ais_bench_root, model_type)
        dataset_refs = [resolve_dataset(runtime.ais_bench_root, name) for name in datasets]

        config_path = make_config_path(runtime, self.tool, self.subcommand)
        config_content = _render(
            runtime=runtime,
            model_ref=model_ref,
            dataset_refs=dataset_refs,
            model_fields=model_fields,
            test_range=test_range,
        )
        accuracy_args = _accuracy_cli_args(
            num_prompts=num_prompts,
            max_workers_per_gpu=max_workers_per_gpu,
            dump_eval_details=dump_eval_details,
            dump_extract_rate=dump_extract_rate,
            merge_ds=merge_ds,
        )

        return PreparedRun(
            tool=self.tool,
            subcommand=self.subcommand,
            work_dir=runtime.work_dir,
            cwd=runtime.ais_bench_root,
            env=build_env(runtime.ais_bench_root),
            command=build_command(runtime, config_path, "all", accuracy_args),
            python_executable=runtime.python_executable,
            config_path=config_path,
            config_content=config_content,
            timeout=runtime.timeout,
            mode="all",
            summary={
                "model_type": model_type,
                "abbr": abbr,
                "datasets": list(datasets),
                "num_prompts": num_prompts,
                "work_dir": str(runtime.work_dir),
            },
            preflight_context={
                "modules": [
                    {
                        "module": "ais_bench.benchmark.partitioners",
                        "attrs": ["NaivePartitioner"],
                    },
                    {
                        "module": "ais_bench.benchmark.runners.local",
                        "attrs": ["LocalRunner"],
                    },
                    {
                        "module": "ais_bench.benchmark.tasks",
                        "attrs": ["OpenICLApiInferTask"],
                    },
                    {
                        "module": "ais_bench.benchmark.configs.summarizers.example",
                        "attrs": ["summarizer"],
                    },
                ],
                "datasets": [
                    {"module": ref.module, "variable": ref.variable}
                    for ref in dataset_refs
                ],
                "model": {
                    "module": model_ref.module,
                    "variable": model_ref.variable,
                    "overrides": model_fields,
                    "check_fields": sorted(model_fields),
                },
            },
        )

    def preflight(self, prepared: PreparedRun):
        from .preflight import build_report

        return build_report(prepared)

    def collect_results(self, prepared: PreparedRun, output_dir: Path) -> dict:
        from .llm_results import collect_llm_results

        return collect_llm_results(prepared, output_dir)


def _accuracy_cli_args(
    *,
    num_prompts: int | None,
    max_workers_per_gpu: int,
    dump_eval_details: bool,
    dump_extract_rate: bool,
    merge_ds: bool,
) -> list[str]:
    args: list[str] = []
    if num_prompts is not None:
        args.extend(["--num-prompts", str(num_prompts)])
    if max_workers_per_gpu != 1:
        args.extend(["--max-workers-per-gpu", str(max_workers_per_gpu)])
    if dump_eval_details:
        args.append("--dump-eval-details")
    if dump_extract_rate:
        args.append("--dump-extract-rate")
    if merge_ds:
        args.append("--merge-ds")
    return args


def _render(
    *,
    runtime: RuntimeOptions,
    model_ref: ConfigRef,
    dataset_refs: list[ConfigRef],
    model_fields: dict,
    test_range: str | None,
) -> str:
    lines = [
        "# 由 AutoEval-Benchmark-Adaptor 自动生成，请勿手工修改",
        "import copy",
        "from mmengine.config import read_base",
        "from ais_bench.benchmark.partitioners import NaivePartitioner",
        "from ais_bench.benchmark.runners.local import LocalRunner",
        "from ais_bench.benchmark.tasks import OpenICLApiInferTask",
        "",
        "with read_base():",
        "    from ais_bench.benchmark.configs.summarizers.example import summarizer",
        f"    from {model_ref.module} import {model_ref.variable} as _base_models",
    ]
    for index, ref in enumerate(dataset_refs):
        lines.append(f"    from {ref.module} import {ref.variable} as _dataset_{index}")

    lines.extend(
        [
            "",
            "models = copy.deepcopy(_base_models)",
            f"models[0].update({as_python(model_fields)})",
        ]
    )

    if len(dataset_refs) == 1:
        lines.append("datasets = copy.deepcopy(_dataset_0)")
    else:
        joined = " + ".join(
            f"copy.deepcopy(_dataset_{index})" for index in range(len(dataset_refs))
        )
        lines.append(f"datasets = {joined}")

    if test_range is not None:
        lines.extend(
            [
                "for _dataset in datasets:",
                '    _dataset.setdefault("reader_cfg", {})["test_range"] = '
                + repr(test_range),
            ]
        )

    lines.extend(
        [
            "",
            "infer = dict(",
            "    partitioner=dict(type=NaivePartitioner),",
            "    runner=dict(type=LocalRunner, task=dict(type=OpenICLApiInferTask)),",
            ")",
            "",
            f"work_dir = {str(runtime.work_dir)!r}",
            "",
        ]
    )
    return "\n".join(lines)
