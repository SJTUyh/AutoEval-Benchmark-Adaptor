"""基于 Harbor 的 Agent 精度测评适配器（AISBench mode=agent）。"""

from __future__ import annotations

import re
from pathlib import Path

from ...config import MISSING, ParamReader
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
    "agent_name": "str",
    "agent_import_path": "str",
    "model_names": "list_str",
    "api_base": "str",
    "api_key": "str",
    "llm_kwargs": "dict",
    "model_info": "dict",
    "agent_kwargs": "dict",
    "agent_env": "dict",
    "deps_path": "str",
    "n_concurrent": "int",
    "concurrency_group": "str",
    "skills": "list",
    "mcp_servers": "list",
    "resume_trajectory": "bool",
    "load_trajectory": "str",
    "extra_allowed_hosts": "list_str",
    "include_logs": "list_str",
    "exclude_logs": "list_str",
    "override_timeout_sec": "float",
    "override_setup_timeout_sec": "float",
    "max_timeout_sec": "float",
}

DATASET_FIELDS = {
    "path": "str",
    "dataset_name_version": "str",
    "n_attempts": "int",
    "n_concurrent_trials": "int",
    "install_only": "bool",
    "metrics": "any",
    "artifacts": "list_str",
    "extra_instruction_paths": "list_str",
    "environment_type": "str",
    "environment_force_build": "bool",
    "environment_delete": "bool",
    "environment_env": "dict",
    "environment_kwargs": "dict",
    "extra_docker_compose": "list_str",
    "override_cpus": "float",
    "override_memory_mb": "int",
    "override_storage_mb": "int",
    "override_gpus": "int",
    "timeout_multiplier": "float",
    "agent_timeout_multiplier": "float",
    "verifier_timeout_multiplier": "float",
    "agent_setup_timeout_multiplier": "float",
    "environment_build_timeout_multiplier": "float",
    "max_retries": "int",
    "retry_include_exceptions": "list_str",
    "retry_exclude_exceptions": "list_str",
    "task_names": "list_str",
    "exclude_task_names": "list_str",
    "n_tasks": "int",
    "disable_verification": "bool",
    "verifier_env": "list_str",
    "verifier_import_path": "str",
    "verifier_kwargs": "dict",
    "env_file": "str",
    "registry_url": "str",
    "registry_path": "str",
    "quiet": "bool",
    "yes": "bool",
}


@register("ais_bench", "harbor_agent")
class AISBenchHarborAgentAdapter(BaseAdapter):
    tool = "ais_bench"
    subcommand = "harbor_agent"

    def prepare(self, params: dict, *, container=None) -> PreparedRun:
        reader = ParamReader(params)

        abbr = reader.take("abbr", required=True)
        dataset_abbr = reader.take("dataset_abbr")
        overrides = reader.take("model_overrides", kind="dict", default={})
        runtime = take_runtime(reader, container=container)

        model_fields: dict = {}
        for key, kind in MODEL_FIELDS.items():
            value = reader.take_optional(key, kind=kind)
            if value is not MISSING:
                model_fields[key] = value

        dataset_args: dict = {}
        for key, kind in DATASET_FIELDS.items():
            value = reader.take_optional(key, kind=kind)
            if value is not MISSING:
                dataset_args[key] = value
        reader.finish()

        if not model_fields.get("agent_name") and not model_fields.get("agent_import_path"):
            raise ConfigError("params 必须提供 agent_name 或 agent_import_path")
        if not dataset_args.get("path") and not dataset_args.get("dataset_name_version"):
            raise ConfigError("params 必须提供 path 或 dataset_name_version")

        model_fields.update(overrides)
        model_fields["abbr"] = abbr
        dataset_args["debug"] = runtime.debug
        dataset_args.setdefault("yes", True)

        resolved_abbr = dataset_abbr or _derive_dataset_abbr(dataset_args)

        config_path = make_config_path(runtime, self.tool, self.subcommand)
        config_content = _render(
            runtime=runtime,
            model_fields=model_fields,
            dataset_args=dataset_args,
            dataset_abbr=resolved_abbr,
        )

        return PreparedRun(
            tool=self.tool,
            subcommand=self.subcommand,
            work_dir=runtime.work_dir,
            cwd=runtime.ais_bench_root,
            env=build_env(runtime.ais_bench_root),
            command=build_command(runtime, config_path, "agent"),
            python_executable=runtime.python_executable,
            config_path=config_path,
            config_content=config_content,
            timeout=runtime.timeout,
            mode="agent",
            container=runtime.container,
            summary={
                "abbr": abbr,
                "agent_name": model_fields.get("agent_name"),
                "dataset_abbr": resolved_abbr,
                "path": dataset_args.get("path"),
                "dataset_name_version": dataset_args.get("dataset_name_version"),
                "work_dir": str(runtime.work_dir),
                "execution": runtime.execution,
                "container": runtime.container.name if runtime.container else None,
            },
            preflight_context={
                "modules": [
                    {
                        "module": (
                            "ais_bench.benchmark.tasks.custom_tasks"
                            ".harbor_agent_task"
                        ),
                        "attrs": ["HarborAgentTask"],
                    },
                    {
                        "module": "ais_bench.benchmark.runners.harbor_runner",
                        "attrs": ["HarborRunner"],
                    },
                    {
                        "module": "ais_bench.benchmark.summarizers.harbor",
                        "attrs": ["HarborSummarizer"],
                    },
                ],
                "harbor": {
                    "agent_name": model_fields.get("agent_name"),
                    "modules": [
                        {"module": "harbor", "attrs": []},
                        {"module": "harbor.models.job.config", "attrs": ["JobConfig"]},
                        {
                            "module": "harbor.models.trial.config",
                            "attrs": ["AgentConfig"],
                        },
                        {
                            "module": "harbor.models.agent.name",
                            "attrs": ["AgentName"],
                        },
                    ],
                    "sources": [
                        (
                            "ais_bench.benchmark.tasks.custom_tasks"
                            ".harbor_agent_task"
                        ),
                        "ais_bench.benchmark.utils.agent_params",
                    ],
                    "tokens": sorted(
                        (set(model_fields) | set(dataset_args))
                        - {"abbr", "yes", "env_file"}
                    ),
                },
            },
        )

    def preflight(self, prepared: PreparedRun):
        from .preflight import build_report

        return build_report(prepared)


def _derive_dataset_abbr(dataset_args: dict) -> str:
    source = dataset_args.get("dataset_name_version")
    if source:
        base = str(source).split("@", 1)[0].rsplit("/", 1)[-1]
    else:
        base = Path(str(dataset_args.get("path", "agent")).rstrip("/\\")).name
    base = re.sub(r"[^0-9A-Za-z_.-]+", "_", base).strip("_") or "agent"
    return f"harbor_{base}"


def _render(
    *,
    runtime: RuntimeOptions,
    model_fields: dict,
    dataset_args: dict,
    dataset_abbr: str,
) -> str:
    models = [model_fields]
    datasets = [dict(abbr=dataset_abbr, args=dataset_args)]
    lines = [
        "# 由 AutoEval-Benchmark-Adaptor 自动生成，请勿手工修改",
        "from ais_bench.benchmark.runners.harbor_runner import HarborRunner",
        "from ais_bench.benchmark.summarizers.harbor import HarborSummarizer",
        "from ais_bench.benchmark.tasks.custom_tasks.harbor_agent_task import HarborAgentTask",
        "",
        f"models = {as_python(models)}",
        "",
        f"datasets = {as_python(datasets)}",
        "",
        "eval = dict(",
        "    runner=dict(",
        "        type=HarborRunner,",
        f"        monitor_port={runtime.monitor_port},",
        "        task=dict(type=HarborAgentTask),",
        "    ),",
        ")",
        "",
        'summarizer = dict(attr="accuracy", type=HarborSummarizer)',
        "",
        f"work_dir = {str(runtime.work_dir)!r}",
        "",
    ]
    return "\n".join(lines)
