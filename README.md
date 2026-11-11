# AutoEval-Benchmark-Adaptor 使用文档

用统一 JSON 参数格式调用不同测评工具。当前已适配 AISBench 的两个子功能：

| tool | subcommand | 说明 | AISBench 运行模式 |
| --- | --- | --- | --- |
| `ais_bench` | `llm_accuracy` | 常规 LLM 精度测评（服务化模型 + 数据集） | `-m all` |
| `ais_bench` | `harbor_agent` | 基于 Harbor 的 Agent 精度测评 | `-m agent` |

适配器只负责：参数校验 → 生成 AISBench 自定义 Python 配置 → 子进程调用 → 落盘运行元数据。不修改 AISBench 仓库，也不解析最终指标。

## 1. 目录结构

```
AutoEval-Benchmark-Adaptor/
├── pyproject.toml
├── examples/
│   ├── ais_bench_llm_accuracy.json
│   └── ais_bench_harbor_agent.json
├── src/autoeval_adaptor/
│   ├── cli.py            # 命令行入口
│   ├── config.py         # 顶层结构与扁平参数校验
│   ├── discovery.py      # 定位 AISBench、解析模型/数据集别名
│   ├── registry.py       # (tool, subcommand) → 适配器
│   ├── preflight.py      # 通用环境探测（目标解释器子进程）
│   ├── template.py       # 内置配置模板加载与合并
│   ├── runner.py         # 子进程、日志、超时、结果文件
│   ├── templates/        # 按 tool/subcommand 分组的配置模板
│   │   └── ais_bench/
│   │       ├── llm_accuracy.json
│   │       └── harbor_agent.json
│   └── tools/
│       ├── base.py
│       └── ais_bench/
│           ├── common.py
│           ├── preflight.py
│           ├── accuracy.py
│           └── harbor_agent.py
└── tests/
```

## 2. 环境与安装

### 2.1 安装适配器

```bash
cd AutoEval-Benchmark-Adaptor
pip install -e .
```

安装后可使用：

```bash
autoeval-adaptor --help
```

不安装也可以直接运行（PowerShell）：

```powershell
$env:PYTHONPATH='src'
python -m autoeval_adaptor --config examples/ais_bench_llm_accuracy.json --dry-run
```

> 适配器本身只依赖 Python 标准库，`mmengine`、`ais_bench`、`harbor` 只在真正执行时由被调用的 Python 环境提供。

### 2.2 AISBench 环境

适配器需要知道 AISBench 仓库根目录（包含 `ais_bench/benchmark/cli/main.py`）。定位顺序：

1. `params.ais_bench_root` 或 CLI `--ais-bench-root`；
2. 当前工作目录下的 `benchmark`、当前工作目录本身、其父目录下的 `benchmark`；
3. 适配器项目根目录及其父目录下的 `benchmark`。

本工作区默认能自动找到 `d:\group_dev\autoeval_group\benchmark`，跨机器使用时建议显式传入。

执行时使用 `params.python_executable`（默认当前解释器）运行：

```text
<python> -m ais_bench.benchmark.cli.main <生成的配置> -m all|agent ...
```

因此该 Python 环境必须安装 AISBench 及其依赖（`mmengine` 等）。如果适配器与 AISBench 不在同一环境，用 `--python` 指向 AISBench 的解释器。

### 2.3 Harbor 前置条件

`harbor_agent` 额外要求（详见 `benchmark/docs/source_zh_cn/extended_benchmark/agent/harbor_bench.md`）：

- Python 3.12 且安装 `harbor==0.6.1`；
- Docker >= 20.10、docker compose >= 2.0；
- 已配置 `harbor/environments/docker/docker-compose-base.yaml`（如 `network_mode: host`、代理、目录挂载）；
- 已准备 Terminal-Bench 数据集及对应镜像（本地 `path` 或远程 `dataset_name_version`）。

这些由使用方准备，适配器不做检查。

### 2.4 容器化执行环境（Docker）

适配器内置每个 `(tool, subcommand)` 的镜像构建与容器启动能力，默认 `execution=auto`：

- 检测到可用 Docker：自动构建缺失镜像、启动容器，再用 `docker exec` 在容器内执行 AISBench；
- 未检测到 Docker 或显式传 `--local`：回退本机执行。

| 子命令 | 基镜像 | 镜像 | 容器启动方式 |
| --- | --- | --- | --- |
| `llm_accuracy` | `ghcr.io/aisbench/agent-runtime:<版本>-ubuntu24.04-py312-<arch>` | `autoeval/ais_bench-llm_accuracy:<版本>-ubuntu24.04-py312-<arch>` | `--net=host`，无需 docker socket |
| `harbor_agent` | 同上 | `autoeval/ais_bench-harbor_agent:<版本>-ubuntu24.04-py312-<arch>` | 默认 socket（挂载 `/var/run/docker.sock`）；`--env-isolation dind` 使用 privileged + 独立 dockerd |

镜像构建说明：

- 默认版本 `v3.1-20261013-master`，可用 `benchmark_version` 覆盖；
- Dockerfile 在基镜像内切换 benchmark tag 并以编辑模式安装（`pip install -e /benchmark`）；
- 同时以编辑模式安装 `autoeval-adaptor` 到 `/opt/autoeval-adaptor`；
- 构建上下文是适配器仓库根目录，因此构建需在源码 checkout 中执行。

常用命令：

```bash
# 构建镜像
autoeval-adaptor --config examples/ais_bench_llm_accuracy.json --build-env

# 构建并启动容器（镜像缺失时自动构建）
autoeval-adaptor --config examples/ais_bench_harbor_agent.json --start-env

# 查看镜像/容器/环境版本
autoeval-adaptor --config examples/ais_bench_harbor_agent.json --env-status

# 停止并删除容器
autoeval-adaptor --config examples/ais_bench_harbor_agent.json --stop-env
```

也可直接使用子命令目录下的脚本：

```text
docker/ais_bench/llm_accuracy/{build.sh,start.sh,stop.sh}
docker/ais_bench/harbor_agent/{build.sh,start.sh,stop.sh}
```

挂载约定：

- docker 模式要求 Linux 宿主绝对路径，容器内使用同路径挂载；
- `work_dir` 自动挂载；
- `harbor_agent` 自动同路径挂载 `path` 数据集与 `deps_path`；
- `llm_accuracy` 可用 `datasets_dir` 挂载到 `/benchmark/ais_bench/datasets:ro`；
- 其它目录用 `mounts: ["/host:/container[:ro]"]` 追加。

`socket` 模式要求 `path`、`deps_path`、`work_dir` 为真实绝对路径（不能含符号链接）；`dind` 要求宿主 Docker >= 20.10（`--cgroupns=host`），不满足时直接报错。


## 3. 统一配置格式

### 3.1 顶层结构

顶层**只允许**三个字段：

```json
{
  "tool": "ais_bench",
  "subcommand": "llm_accuracy",
  "params": {
    "...": "该子命令的全部参数平铺在这里"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `tool` | string | 是 | 测评工具名称，当前支持 `ais_bench` |
| `subcommand` | string | 是 | 工具子功能，当前支持 `llm_accuracy`、`harbor_agent` |
| `params` | object | 是 | 子命令参数，全部平铺，不按 model/dataset 再分组 |

### 3.2 校验规则

- 顶层多余字段报错，例如把 `work_dir` 写到 `params` 外面会被拒绝；
- `params` 中未声明的字段报错（防止拼写错误被静默忽略）；
- 类型严格校验：整数不接受字符串或布尔值，`string[]` 必须全为字符串；
- `tool` / `subcommand` 未注册时报错并列出已支持的组合。

### 3.3 配置模板

每个 `(tool, subcommand)` 都有一份内置配置模板，存放于：

```text
src/autoeval_adaptor/templates/<tool>/<subcommand>.json
```

默认会加载对应模板并与输入的 JSON 合并，因此基准目录中的 `standard_config.json` 只需要写基准特有参数即可。

合并规则：

- 以模板为底，输入配置优先；
- `dict` 递归合并（例如 `generation_kwargs`、`llm_kwargs` 可只覆盖部分键）；
- `list` 和标量整体替换；
- 模板与输入配置的 `tool` / `subcommand` 必须一致。

内置模板提供的通用必备参数：

| 模板 | 参数 |
| --- | --- |
| `ais_bench/llm_accuracy` | `abbr`、`model_type`、`model`、`path`、`host_ip`、`host_port`、`api_key`、`stream`、`request_rate`、`retry`、`max_out_len`、`batch_size`、`max_num_workers`、`work_dir` |
| `ais_bench/harbor_agent` | `abbr`、`model_names`、`api_base`、`api_key`、`n_attempts`、`n_concurrent_trials`、`environment_type`、`environment_delete`、`max_num_workers`、`work_dir` |

示例：直接使用基准目录的 `standard_config.json`：

```bash
autoeval-adaptor --config ../AutoEval-Standard/DeepSeek-V4.1-Flash-1017/DeepSWE/standard_config.json --dry-run
autoeval-adaptor --config ../AutoEval-Standard/DeepSeek-V4.1-Flash-1017/GPQA_Diamond/standard_config.json --dry-run
```

模板相关开关：

| 开关 | 说明 |
| --- | --- |
| 默认 | 自动加载内置模板并合并 |
| `--no-template` | 不使用模板，只认输入 JSON（缺少必备参数会报错） |
| `--template PATH` | 使用自定义模板替代内置模板 |
| `--show-template` | 打印当前 `tool/subcommand` 使用的模板后退出 |

> 模板中的 `abbr`、`model_names`、`api_base`、`work_dir` 是通用默认值；实际模型名、服务地址和输出目录请在 `standard_config.json` 或 CLI 覆盖参数中指定。

## 4. 子功能：llm_accuracy

### 4.1 示例

```json
{
  "tool": "ais_bench",
  "subcommand": "llm_accuracy",
  "params": {
    "abbr": "deepseek-v4.1-flash-gpqa",
    "model_type": "vllm_api_general_chat",
    "model": "DeepSeek-V4.1-Flash",
    "host_ip": "127.0.0.1",
    "host_port": 8080,
    "api_key": "",
    "max_out_len": 32768,
    "batch_size": 8,
    "retry": 2,
    "generation_kwargs": {
      "temperature": 0.6,
      "top_p": 0.95
    },
    "datasets": ["gpqa_gen_0_shot_cot_chat_prompt"],
    "dataset_test_range": "[0:100]",
    "max_num_workers": 4,
    "work_dir": "outputs/ais_bench_llm_accuracy"
  }
}
```

### 4.2 参数表

**模型标识与数据集**

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `abbr` | string | 是 | - | 模型唯一标识，写入配置并作为结果列名 |
| `model_type` | string | 是 | - | AISBench 模型配置文件名，如 `vllm_api_general_chat` |
| `datasets` | string[] | 是 | - | 数据集配置文件名列表，如 `gpqa_gen_0_shot_cot_chat_prompt` |
| `dataset_test_range` | string | 否 | 不设置 | 为所有数据集写入 `reader_cfg.test_range`，如 `"[0:100]"` |
| `model_overrides` | object | 否 | `{}` | AISBench 模型字段直通，优先级最高（`abbr` 除外） |

**模型字段（仅覆盖传入项，其余继承基础模型配置）**

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `path` | string | 模型路径，服务化模型通常为 `""` |
| `model` | string | 服务端模型名 |
| `stream` | bool | 是否流式 |
| `request_rate` | number | 请求速率限制，0 表示不限 |
| `use_timestamp` | bool | 请求是否携带时间戳 |
| `retry` | int | 失败重试次数 |
| `api_key` | string | 服务 API Key |
| `host_ip` | string | 服务地址 |
| `host_port` | int | 服务端口 |
| `url` | string | 自定义接口路径 |
| `max_out_len` | int | 最大输出 token 数 |
| `batch_size` | int | 推理 batch size |
| `trust_remote_code` | bool | 是否信任远程代码 |
| `generation_kwargs` | object | 生成参数，如 `temperature`、`top_p` |

**精度运行参数（映射为 AISBench CLI 参数）**

| 参数 | 类型 | 默认 | 对应 CLI | 说明 |
| --- | --- | --- | --- | --- |
| `num_prompts` | int | 不传 | `--num-prompts` | 每个数据集只推理前 N 条；会覆盖配置中的 `dataset_test_range` |
| `max_workers_per_gpu` | int | `1` | `--max-workers-per-gpu` | 单 GPU 并行任务数，仅本地 runner 有意义 |
| `dump_eval_details` | bool | `false` | `--dump-eval-details` | 输出逐样本正确性与 bpb 等明细 |
| `dump_extract_rate` | bool | `false` | `--dump-extract-rate` | 输出答案抽取率 |
| `merge_ds` | bool | `false` | `--merge-ds` | 合并 mmlu/ceval 等多文件数据集 |

> `num_prompts` 与 `dataset_test_range` 同时设置时，AISBench 会用 `num_prompts` 重写 `reader_cfg.test_range`，以 `num_prompts` 为准。

**模型类型限制**

`model_type` 只能选择服务化模型目录下的配置：

- `vllm_api`
- `mindie_api`
- `tgi_api`
- `triton_api`

本地/HuggingFace 模型（`hf_models`、`vllm_offline_models`、`lmm_models`）需要不同的推理任务，当前版本不支持。

`model_type` 可以带目录前缀，如 `vllm_api/vllm_api_general_chat`，解析时取最后一段文件名。

### 4.3 映射说明

- 适配器先导入 `ais_bench/benchmark/configs/models/<category>/<model_type>.py` 中的 `models` 作为基础配置，再覆盖 `params` 中传入的模型字段，因此未传字段保留 AISBench 默认值。
- 每个数据集别名解析为 `ais_bench/benchmark/configs/datasets/**/<alias>.py`（也扫描 `dataset_collections`），取文件中以 `_datasets` 结尾的变量导入。
- 别名按文件名解析，必须唯一；同名文件出现在多个目录时会报错并列出候选。

### 4.4 生成的配置示意

```python
# 由 AutoEval-Benchmark-Adaptor 自动生成，请勿手工修改
import copy
from mmengine.config import read_base
from ais_bench.benchmark.partitioners import NaivePartitioner
from ais_bench.benchmark.runners.local import LocalRunner
from ais_bench.benchmark.tasks import OpenICLApiInferTask

with read_base():
    from ais_bench.benchmark.configs.summarizers.example import summarizer
    from ais_bench.benchmark.configs.models.vllm_api.vllm_api_general_chat import models as _base_models
    from ais_bench.benchmark.configs.datasets.gpqa.gpqa_gen_0_shot_cot_chat_prompt import gpqa_datasets as _dataset_0

models = copy.deepcopy(_base_models)
models[0].update({'model': 'DeepSeek-V4.1-Flash',
 'host_ip': '127.0.0.1',
 'host_port': 8080,
 'max_out_len': 32768,
 'batch_size': 8,
 'generation_kwargs': {'temperature': 0.6, 'top_p': 0.95},
 'abbr': 'deepseek-v4.1-flash-gpqa'})

datasets = copy.deepcopy(_dataset_0)
for _dataset in datasets:
    _dataset.setdefault("reader_cfg", {})["test_range"] = '[0:100]'

infer = dict(
    partitioner=dict(type=NaivePartitioner),
    runner=dict(type=LocalRunner, task=dict(type=OpenICLApiInferTask)),
)

work_dir = 'D:\\...\\outputs\\ais_bench_llm_accuracy'
```

## 5. 子功能：harbor_agent

### 5.1 示例

```json
{
  "tool": "ais_bench",
  "subcommand": "harbor_agent",
  "params": {
    "abbr": "terminus-2",
    "agent_name": "terminus-2",
    "model_names": ["hosted_vllm/DeepSeek-V4.1-Flash"],
    "api_base": "http://127.0.0.1:8080/v1",
    "llm_kwargs": {"max_tokens": 8192},
    "model_info": {
      "max_input_tokens": 128000,
      "max_output_tokens": 8192,
      "input_cost_per_token": 0.0,
      "output_cost_per_token": 0.0
    },
    "path": "/data/terminal-bench-2",
    "n_attempts": 1,
    "n_concurrent_trials": 5,
    "environment_type": "docker",
    "n_tasks": 10,
    "dataset_abbr": "harbor_terminal-bench-2",
    "max_num_workers": 1,
    "work_dir": "outputs/ais_bench_harbor_agent",
    "timeout": 86400
  }
}
```

### 5.2 Agent / 模型参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `abbr` | string | 是 | 模型唯一标识 |
| `agent_name` | string | 二选一 | Harbor Agent 名称，如 `terminus-2`、`claude-code` |
| `agent_import_path` | string | 二选一 | 自定义 Agent 导入路径 `module.path:ClassName` |
| `model_names` | string[] | 否 | 模型名列表，如 `["hosted_vllm/qwen3"]` |
| `api_base` | string | 否 | 模型服务 base url，统一语义参数 |
| `api_key` | string | 否 | 模型服务 API Key |
| `llm_kwargs` | object | 否 | LLM 调用参数，如 `{"max_tokens": 8192}` |
| `model_info` | object | 否 | token 上限与成本信息 |
| `agent_kwargs` | object | 否 | Agent 私有参数，优先级高于统一参数转换结果 |
| `agent_env` | object | 否 | 传给 Agent 的环境变量 |
| `deps_path` | string | 否 | 离线 Agent 依赖包路径 |
| `n_concurrent` | int | 否 | 每个 Agent 并发上限 |
| `concurrency_group` | string | 否 | 并发分组标识，透传 Harbor `AgentConfig` |
| `skills` | array | 否 | Skill 目录或 git 源列表 |
| `mcp_servers` | array | 否 | MCP 服务器配置列表 |
| `resume_trajectory` | bool | 否 | 多步任务跨步恢复 Agent 会话 |
| `load_trajectory` | string | 否 | 预加载轨迹文件路径 |
| `extra_allowed_hosts` | string[] | 否 | 额外允许的 host/IP |
| `include_logs` | string[] | 否 | 需要保留的 Agent 日志 glob |
| `exclude_logs` | string[] | 否 | 需要排除的 Agent 日志 glob |
| `override_timeout_sec` | number | 否 | 覆盖 Agent 超时 |
| `override_setup_timeout_sec` | number | 否 | 覆盖 Agent 初始化超时 |
| `max_timeout_sec` | number | 否 | 最大超时上限 |
| `model_overrides` | object | 否 | AISBench 模型字段直通，优先级最高（`abbr` 除外） |

### 5.3 数据集 / 任务参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `path` | string | 二选一 | 本地数据集路径（也支持单个 task 目录） |
| `dataset_name_version` | string | 二选一 | 远程数据集，如 `name@version`、`org/name@ref` |
| `dataset_abbr` | string | 否 | 结果中的数据集标识；不传则自动生成 |
| `n_attempts` | int | 否 | 每个 trial 的尝试次数（pass@k） |
| `n_concurrent_trials` | int | 否 | 并发 trial 数 |
| `install_only` | bool | 否 | 只构建/安装环境，不执行 Agent |
| `metrics` | any | 否 | 透传 `JobConfig.metrics`，格式随 Harbor 版本 |
| `artifacts` | string[] | 否 | 需要收集的产物路径/名称 |
| `extra_instruction_paths` | string[] | 否 | 追加的 instruction 文件路径 |
| `environment_type` | string | 否 | `docker` / `daytona` / `e2b` / `modal` 等 |
| `environment_force_build` | bool | 否 | 是否强制重建环境 |
| `environment_delete` | bool | 否 | 完成后是否删除环境 |
| `environment_env` | object | 否 | 注入容器的环境变量 |
| `environment_kwargs` | object | 否 | 环境附加参数 |
| `extra_docker_compose` | string[] | 否 | 追加的 docker compose overlay 文件 |
| `override_cpus` | number | 否 | 覆盖容器 CPU 配额 |
| `override_memory_mb` | int | 否 | 覆盖容器内存（MB） |
| `override_storage_mb` | int | 否 | 覆盖容器存储（MB） |
| `override_gpus` | int | 否 | 覆盖容器 GPU 数 |
| `timeout_multiplier` | number | 否 | 所有超时的统一倍数 |
| `agent_timeout_multiplier` | number | 否 | Agent 超时倍数 |
| `verifier_timeout_multiplier` | number | 否 | 验证器超时倍数 |
| `agent_setup_timeout_multiplier` | number | 否 | Agent 初始化超时倍数 |
| `environment_build_timeout_multiplier` | number | 否 | 环境构建超时倍数 |
| `max_retries` | int | 否 | 最大重试次数 |
| `retry_include_exceptions` | string[] | 否 | 仅对这些异常重试 |
| `retry_exclude_exceptions` | string[] | 否 | 这些异常不重试 |
| `task_names` | string[] | 否 | 包含的任务名，支持 glob |
| `exclude_task_names` | string[] | 否 | 排除的任务名，支持 glob |
| `n_tasks` | int | 否 | 最大任务数量 |
| `disable_verification` | bool | 否 | 关闭验证器 |
| `verifier_env` | string[] | 否 | 验证器环境变量，`KEY=VALUE` 列表 |
| `verifier_import_path` | string | 否 | 自定义验证器 `module:Class` |
| `verifier_kwargs` | object | 否 | 验证器附加参数 |
| `env_file` | string | 否 | `.env` 文件路径（兼容 AISBench CLI 保留字段） |
| `registry_url` | string | 否 | 远程数据集 registry 地址 |
| `registry_path` | string | 否 | 本地 registry 路径 |
| `quiet` | bool | 否 | 静默模式 |
| `yes` | bool | 否 | 自动确认环境变量提示，默认 `True`（兼容 AISBench CLI 保留字段） |

> 当前一个配置文件只生成一个 Harbor 数据集条目。需要同时跑多个数据集时，分别提交多份 JSON。

### 5.4 映射说明

- 模型侧字段写入 `models[0]`，数据集侧字段写入 `datasets[0].args`，`debug` 由公共运行参数注入到 `args`。
- `dataset_abbr` 缺省规则：
  - 有 `dataset_name_version`：取其 `@` 前的最后一段；
  - 否则取 `path` 的目录名；
  - 非法字符替换为 `_`，最终加前缀 `harbor_`。
- 生成的配置使用 `HarborRunner` + `HarborAgentTask` + `HarborSummarizer`，以 `-m agent` 运行，不执行原生 infer 阶段。

### 5.5 生成的配置示意

```python
# 由 AutoEval-Benchmark-Adaptor 自动生成，请勿手工修改
from ais_bench.benchmark.runners.harbor_runner import HarborRunner
from ais_bench.benchmark.summarizers.harbor import HarborSummarizer
from ais_bench.benchmark.tasks.custom_tasks.harbor_agent_task import HarborAgentTask

models = [{'agent_name': 'terminus-2',
  'model_names': ['hosted_vllm/DeepSeek-V4.1-Flash'],
  'api_base': 'http://127.0.0.1:8080/v1',
  'llm_kwargs': {'max_tokens': 8192},
  'model_info': {'max_input_tokens': 128000,
                 'max_output_tokens': 8192,
                 'input_cost_per_token': 0.0,
                 'output_cost_per_token': 0.0},
  'abbr': 'terminus-2'}]

datasets = [{'abbr': 'harbor_terminal-bench-2',
  'args': {'path': '/data/terminal-bench-2',
           'n_attempts': 1,
           'n_concurrent_trials': 5,
           'environment_type': 'docker',
           'n_tasks': 10,
           'debug': False,
           'yes': True}}]

eval = dict(
    runner=dict(
        type=HarborRunner,
        monitor_port=0,
        task=dict(type=HarborAgentTask),
    ),
)

summarizer = dict(attr="accuracy", type=HarborSummarizer)

work_dir = 'D:\\...\\outputs\\ais_bench_harbor_agent'
```

## 6. 公共运行参数

以下参数在 `params` 中平铺，`llm_accuracy` 与 `harbor_agent` 通用：

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `work_dir` | string | 是 | - | 输出根目录，相对路径按适配器当前目录解析为绝对路径 |
| `max_num_workers` | int | 否 | `1` | 传给 AISBench `--max-num-workers` |
| `debug` | bool | 否 | `false` | 传 `--debug`；`harbor_agent` 同时写入 `datasets[].args.debug` |
| `reuse` | string | 否 | 不传 | 传 `--reuse`，值为 `latest` 或时间戳 |
| `purge_exception_cases` | bool | 否 | `false` | 传 `--purge-exception-cases`，配合 `reuse` 清理异常用例后重跑 |
| `timeout` | number | 否 | 无限制 | 适配器等待子进程的秒数，超时终止并返回 124 |
| `monitor_port` | int | 否 | `0` | Harbor 监控端口，仅 `harbor_agent` 生效 |
| `ais_bench_root` | string | 否 | 自动探测 | AISBench 仓库根目录 |
| `python_executable` | string | 否 | 当前解释器 | 本机执行时运行 AISBench 的 Python |
| `extra_cli_args` | string[] | 否 | `[]` | 追加到 AISBench 命令末尾，如 `["--dump-eval-details"]` |
| `execution` | string | 否 | `auto` | `auto`（有 Docker 用容器）/`docker`/`local` |
| `docker_image` | string | 否 | 自动 | 覆盖运行镜像 |
| `base_image` | string | 否 | 自动 | 覆盖构建设基镜像 |
| `container_name` | string | 否 | 自动 | 覆盖容器名 |
| `benchmark_version` | string | 否 | `v3.1-20261013-master` | 镜像 tag 与 Dockerfile 构建参数 |
| `mounts` | string[] | 否 | `[]` | `host:container[:ro]` 额外挂载 |
| `env_isolation` | string | 否 | `socket` | 仅 `harbor_agent`：`socket`/`dind` |
| `case_images` | string | 否 | 空 | 仅 dind：`docker save` 镜像包 |
| `datasets_dir` | string | 否 | 空 | 仅 `llm_accuracy`：挂到 `/benchmark/ais_bench/datasets:ro` |
| `keep_container` | bool | 否 | `true` | 运行结束后保留容器 |

## 7. 命令行

```text
autoeval-adaptor --config <json> [--dry-run] [--preflight-only] [--skip-preflight]
                 [--template PATH | --no-template] [--show-template]
                 [--work-dir DIR] [--ais-bench-root DIR] [--python EXE]
```

| 参数 | 说明 |
| --- | --- |
| `--config` | 必填，统一 JSON 配置路径 |
| `--dry-run` | 只打印生成的配置与执行命令，不创建目录、不执行；默认跳过预检 |
| `--preflight-only` | 只执行参数预检并打印结果，不运行测评 |
| `--skip-preflight` | 执行前跳过参数预检 |
| `--template` | 使用指定的配置模板文件，替代内置模板 |
| `--no-template` | 不使用配置模板，只使用输入 JSON 中的参数 |
| `--show-template` | 打印当前 `tool/subcommand` 使用的模板后退出 |
| `--work-dir` | 覆盖 `params.work_dir` |
| `--ais-bench-root` | 覆盖 `params.ais_bench_root` |
| `--python` | 覆盖 `params.python_executable` |
| `--local` | 强制本机执行 |
| `--build-env` | 构建当前子命令对应的镜像 |
| `--start-env` | 构建（缺失时）并启动容器 |
| `--stop-env` | 停止并删除容器 |
| `--env-status` | 打印镜像/容器/环境版本状态 |
| `--force-rebuild` | 配合 `--build-env` 使用 `--no-cache` |
| `--env-isolation` | 覆盖 `params.env_isolation`（仅 `harbor_agent`） |

覆盖优先级：CLI > `params`。

示例：

```bash
# 预览生成的配置
autoeval-adaptor --config examples/ais_bench_llm_accuracy.json --dry-run

# 只检查参数与目标环境是否兼容
autoeval-adaptor --config examples/ais_bench_llm_accuracy.json --preflight-only

# 指定 AISBench 所在 Python 环境
autoeval-adaptor --config examples/ais_bench_harbor_agent.json \
  --python /opt/conda/envs/ais_bench_py312/bin/python

# 指定输出目录与 AISBench 位置
autoeval-adaptor --config config.json \
  --work-dir /data/runs/gpqa --ais-bench-root /opt/aisbench/benchmark
```

### 7.1 参数预检

预检与工具绑定：每个适配器实现自己的 `preflight()`，AISBench 适配器会使用**实际执行时的解释器、cwd 和 PYTHONPATH** 启动探测子进程，检查配置参数是否被目标环境中安装的 AISBench/Harbor 支持。

默认行为：

- 正常执行前自动预检；`--dry-run` 默认跳过；
- 预检出现 `error` 时阻止执行并返回退出码 2；
- 仅 `warning` 时继续执行；
- `--preflight-only` 只输出报告；
- `--skip-preflight` 完全跳过；
- 预检通过后随执行写入 `adaptor_result.json` 的 `preflight` 字段；`--preflight-only` 仅打印，不落盘。

AISBench 预检项：

| 分类 | 检查内容 | 级别 |
| --- | --- | --- |
| 解释器 | 目标 Python 是否可启动、版本 | error（无法启动） |
| AISBench | `ais_bench` 是否可导入、版本、安装路径 | error |
| mmengine | 是否可导入、版本 | warning |
| CLI 参数 | 从实际 `ArgumentParser` 反射出支持的选项，逐个校验本次命令用到的 flag | error |
| 运行模式 | `-m all` / `-m agent` 是否在当前 CLI 的 `--mode` 可选值中 | error |
| 数据集配置 | 数据集模块是否存在、`_datasets` 变量是否存在 | error |
| 模型配置 | 基础模型配置是否可导入、AISBench `_validate_model_cfg` 是否通过 | error |
| 模型字段 | 覆盖字段是否被模型类 `__init__` 接受（排除 `build_model_from_cfg` 的配置级字段） | error |
| Harbor 运行时 | `harbor` 包、`JobConfig` / `AgentConfig` / `AgentName` 是否可导入 | error |
| Harbor Agent | `agent_name` 是否在当前 harbor 的 `AgentName` 列表中 | error |
| Harbor 参数能力 | 本次使用的参数是否被当前 AISBench 的 Harbor 实现引用 | warning |

探测按阶段执行，单个阶段超时（基础阶段 30 秒，其余阶段 60 秒）只会让该阶段判定失败，不会无限等待；CLI 参数等基础信息仍会保留在报告中。

示例输出：

```text
[preflight] ais_bench/llm_accuracy
[preflight] python: 3.12.4 (/opt/conda/envs/ais_bench/bin/python)
[preflight] ais_bench: 3.1.0 (/opt/aisbench/benchmark/ais_bench/__init__.py)
  [PASS] AISBench 导入: 版本 3.1.0
  [PASS] mmengine: 版本 0.10.7
  [PASS] CLI 参数: 4 个参数均被支持
  [PASS] 运行模式: -m all
  [PASS] 数据集配置 ais_bench.benchmark.configs.datasets.gpqa...: 模块与变量可导入
  [PASS] 模块 ais_bench.benchmark.tasks: 导入成功
  [PASS] 模型参数: VLLMCustomAPIChat 配置校验通过
  [PASS] 模型字段: 8 个字段均在支持范围内
[preflight] 结果: 8 passed, 0 warning(s), 0 error(s)
```

局限性：

- Harbor 参数能力通过检查当前实现源码是否引用参数名判断，属于启发式检查；缺失时给出 warning，不阻断；
- 预检只验证导入、签名与配置校验，不会连接模型服务或 Docker，也不会执行真实测评；
- 老旧 AISBench 可能没有 `--purge-exception-cases` 等参数，预检会在 CLI 参数项直接报 error。

## 8. 执行流程与产物

### 8.1 执行流程

1. 读取 JSON，并合并对应 `(tool, subcommand)` 的内置配置模板；
2. 按 `(tool, subcommand)` 选择适配器；
3. 校验参数、解析模型/数据集别名；
4. 渲染 AISBench Python 配置；
5. 执行参数预检（可通过 `--skip-preflight` 跳过）；
6. 以 `cwd = ais_bench_root`、`PYTHONPATH = ais_bench_root` 启动子进程；
7. 实时透传 stdout/stderr 到终端并写入日志；
8. 识别 AISBench 生成的时间戳目录，写出结果文件。

### 8.2 产物目录

```text
<work_dir>/
├── .adaptor/
│   └── ais_bench_<subcommand>_<时间戳>_<pid>.py   # 适配器生成的原始配置
├── adaptor.log                                    # 完整执行日志
├── adaptor_result.json                            # 本次运行元数据
└── <YYYYMMDD_HHMMSS>/                             # AISBench 自身输出
    ├── configs/                                   # AISBench 落盘的配置副本
    ├── logs/
    ├── results/
    └── summary/                                   # 精度结果表（csv/md/txt）
```

Harbor 场景的 `results/<model>/<dataset>/` 下还包含 `details/`、`trial_*/` 等轨迹与验证信息。

### 8.3 adaptor_result.json

```json
{
  "tool": "ais_bench",
  "subcommand": "llm_accuracy",
  "status": "success",
  "exit_code": 0,
  "command": ["python", "-m", "ais_bench.benchmark.cli.main", "..."],
  "cwd": "D:\\group_dev\\autoeval_group\\benchmark",
  "generated_config": "D:\\...\\outputs\\run\\.adaptor\\ais_bench_llm_accuracy_20261110_105950_31672.py",
  "ais_bench_work_dir": "D:\\...\\outputs\\run\\20261110_105951",
  "log_path": "D:\\...\\outputs\\run\\adaptor.log",
  "started_at": "2026-11-10T10:59:50+08:00",
  "finished_at": "2026-11-10T11:05:12+08:00",
  "duration_sec": 322.14,
  "error": null,
  "params": {
    "model_type": "vllm_api_general_chat",
    "abbr": "deepseek-v4.1-flash-gpqa",
    "datasets": ["gpqa_gen_0_shot_cot_chat_prompt"],
    "work_dir": "D:\\...\\outputs\\run",
    "template": "builtin:ais_bench/llm_accuracy"
  },
  "preflight": {
    "ok": true,
    "checks": [
      {"name": "CLI 参数", "status": "pass", "message": "4 个参数均被支持"}
    ],
    "environment": {"ais_bench": "3.1.0 (D:\\...\\benchmark\\ais_bench\\__init__.py)"}
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `status` | `success` / `failed` / `timeout` / `error` |
| `exit_code` | AISBench 退出码；超时为 124 |
| `ais_bench_work_dir` | AISBench 本次时间戳目录；未识别到则为 `null` |
| `error` | 失败原因（超时、无法启动进程等） |
| `params` | 本次调用的关键参数摘要 |
| `preflight` | 本次预检报告；未执行预检时为 `null` |

使用 `--reuse` 复用已有时间戳目录时，若执行成功且没有新目录，会回退为最近修改的时间戳目录。

### 8.4 状态与退出码

| 场景 | status | CLI 退出码 |
| --- | --- | --- |
| AISBench 正常结束 | `success` | 0 |
| `--dry-run` | `dry_run` | 0 |
| AISBench 非 0 退出 | `failed` | AISBench 退出码 |
| 超过 `timeout` | `timeout` | 124 |
| 参数预检未通过 | - | 2 |
| JSON 校验、tool/subcommand、别名解析失败 | - | 2 |
| 无法创建工作目录或启动子进程 | `error` | 3 |

## 9. 常见用法

### 9.1 先预览再执行

```bash
autoeval-adaptor --config examples/ais_bench_llm_accuracy.json --dry-run
```

预览会打印完整配置内容与命令，适合在提交长任务前核对字段落点。

### 9.2 小样本快速验证

```json
"datasets": ["gpqa_gen_0_shot_cot_chat_prompt"],
"dataset_test_range": "[0:8]",
"max_num_workers": 1
```

Harbor 场景可用 `n_tasks` 限制任务数：

```json
"n_tasks": 3,
"n_concurrent_trials": 1
```

### 9.3 多数据集

`llm_accuracy` 的 `datasets` 支持多个别名，会合并为一个数据集列表：

```json
"datasets": [
  "gpqa_gen_0_shot_cot_chat_prompt",
  "aime2025_gen_0_shot_llmjudge"
]
```

同一份配置中模型与数据集按 AISBench 默认笛卡尔积组合。

### 9.4 透传 AISBench 原生字段

模型侧未覆盖的字段可通过 `model_overrides` 传入：

```json
"model_overrides": {
  "stream": true,
  "request_rate": 5
}
```

`model_overrides` 会覆盖同名显式参数，但 `abbr` 始终以顶层 `abbr` 为准。

> `type`、`pred_postprocessor` 等需要 Python 类对象的字段无法用 JSON 表达，`model_overrides` 不支持；这类扩展需要直接修改 AISBench 基础配置。

### 9.5 中断续跑

```json
"reuse": "20261110_105951"
```

或 `"reuse": "latest"`。Harbor 会跳过已完成的 trial。

需要清理上次异常用例后重跑时：

```json
"reuse": "latest",
"purge_exception_cases": true
```

### 9.6 执行前预检

```bash
# 只检查参数与目标环境是否兼容，不执行测评
autoeval-adaptor --config examples/ais_bench_harbor_agent.json --preflight-only

# 环境已确认，跳过预检直接执行
autoeval-adaptor --config examples/ais_bench_harbor_agent.json --skip-preflight
```

## 10. 常见错误与排查

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `顶层只允许 tool、subcommand、params` | 参数写到了 `params` 外层 | 把所有子命令参数移入 `params` |
| `params 存在未知参数: xxx` | 字段名拼错或该子功能不支持 | 对照参数表；模型扩展字段用 `model_overrides` |
| `未找到 AISBench 根目录` | 自动探测失败 | 传 `--ais-bench-root`，确认包含 `ais_bench/benchmark/cli/main.py` |
| `未找到模型配置/数据集配置` | 别名与文件名不一致 | 检查 `configs/models`、`configs/datasets` 下的 `.py` 文件名 |
| `命中多个文件` | 不同目录存在同名配置 | 保证别名在工作区唯一，按报错列出的候选清理冲突 |
| `仅支持服务化模型配置` | `model_type` 指向本地模型 | 改用 `vllm_api` / `mindie_api` / `tgi_api` / `triton_api` |
| `agent_name 或 agent_import_path` | Harbor 模型未指定 Agent | 二选一补充 |
| `path 或 dataset_name_version` | Harbor 未指定数据集 | 补充本地路径或远程数据集 |
| 子进程报 `No module named ais_bench/mmengine` | 解释器环境不对 | 用 `--python` 指向安装 AISBench 的环境 |
| 日志出现 Docker/Compose 相关错误 | Harbor 环境未准备 | 按 `harbor_bench.md` 检查 Docker、镜像、compose 配置 |
| `status=timeout` | 超过 `params.timeout` | 调大 `timeout`，或检查服务/Agent 是否卡住 |
| 预检报 `当前 AISBench CLI 不支持: --xxx` | 目标环境版本较旧或该参数不属于当前模式 | 升级 AISBench，或移除该参数/改用 `--skip-preflight` |
| 预检报 `模型字段 ... 不接受` | 参数与安装版本的模型类不匹配 | 对照该版本模型配置字段调整参数 |
| 预检报 `目标环境探测超时` | 目标环境导入 AISBench 重型依赖过慢或失败 | 先在该环境执行 `python -c "import ais_bench.benchmark"` 排查，或 `--skip-preflight` |

排查顺序建议：`--dry-run` 看配置 → 看 `adaptor.log` → 看 `<work_dir>/<ts>/logs/` → 复现 AISBench 原生命令。

## 11. 扩展新的测评工具

新增一个工具或子功能只需实现一个适配器并注册，核心流程不用改：

```python
from autoeval_adaptor.registry import register
from autoeval_adaptor.tools.base import BaseAdapter, PreparedRun


@register("my_tool", "my_subcommand")
class MyAdapter(BaseAdapter):
    tool = "my_tool"
    subcommand = "my_subcommand"

    def prepare(self, params: dict) -> PreparedRun:
        # 1. 用 ParamReader 校验并读取参数
        # 2. 生成原生配置文件内容
        # 3. 拼装 command、cwd、env、work_dir
        return PreparedRun(
            tool=self.tool,
            subcommand=self.subcommand,
            work_dir=...,
            cwd=...,
            env=...,
            command=[...],
            config_path=...,
            config_content=...,
            timeout=...,
            summary={...},
        )
```

然后在 `src/autoeval_adaptor/tools/__init__.py` 中导入该模块即可完成注册。若新工具需要统一的指标解析，可在 `runner.py` 的结果落盘之后追加解析步骤。

预检同样与工具绑定：在适配器中实现 `preflight(prepared)` 返回 `PreflightReport`；未实现时使用 `BaseAdapter` 的默认实现（只产生一条 pass 记录）。AISBench 的通用探测逻辑位于 `tools/ais_bench/preflight.py`，新工具可以按相同方式构造探测规格并复用 `run_probe`。

## 12. 与 AISBench 版本的兼容性

已按 `benchmark` 仓库 `c4e6957`（2026-11-10）重新复核全部映射参数，结论如下。

**无破坏性变更**

- 适配器使用的 `models` 字段、agent 模型字段、`datasets[].args`、`HarborRunner` 配置、`-m all` / `-m agent` 工作流均保持有效；
- 模型配置与数据集配置的目录结构和别名解析规则未变。

**本次补齐的参数**

- `llm_accuracy` 运行参数：`num_prompts`、`max_workers_per_gpu`、`dump_eval_details`、`dump_extract_rate`、`merge_ds`；
- `harbor_agent` 模型参数：`concurrency_group`；
- `harbor_agent` 任务参数：`install_only`、`metrics`、`artifacts`、`extra_instruction_paths`、`environment_env`、`extra_docker_compose`、`override_cpus`、`override_memory_mb`、`override_storage_mb`、`override_gpus`、`verifier_import_path`、`verifier_kwargs`、`registry_url`、`registry_path`；
- 公共运行参数：`purge_exception_cases`。

**修正**

- `verifier_env` 在 AISBench 中按 `KEY=VALUE` 字符串列表处理，适配器已由 object 改为 string[]。

**不受本次更新影响**

- 新版 `--batch-size` / `--num-prompts` 多值 sweep 仅支持 `perf` / `perf_viz`，本适配器使用 `all` / `agent` 模式，不会触发多档展开；
- Harbor 计分口径调整（异常用例参与计分、平均分分母改为全部 trial）只影响结果指标，不涉及配置参数；
- `LocalRunner` 新增的 `sweep_cooldown_s` 仅用于 perf 多档串行，适配器不注入该字段。

## 13. 测试

```bash
python -m pytest -q
```

当前覆盖：

- 顶层结构、未知字段、类型与必填校验；
- 内置配置模板存在性、工具匹配校验、嵌套 dict 合并与用户覆盖优先级；
- 基准 `standard_config.json` 直接合并模板后可完成配置渲染；`--no-template`、`--template`、`--show-template` 行为；
- AISBench 根目录定位、模型/数据集别名解析、多命中与非服务化模型报错；
- 两个子功能的配置渲染、参数落点、命令拼装；
- 参数预检：CLI 参数/运行模式、模块与数据集、模型字段与配置校验、Harbor Agent 与参数能力、探测失败阻断执行；
- 精度 CLI 参数（`num_prompts`、`max_workers_per_gpu`、明细导出、`merge_ds`）；
- Harbor 高级任务参数、`concurrency_group`、`verifier_env` 列表格式、`reuse` + `purge_exception_cases`；
- 执行成功、非 0 退出、超时、`--reuse` 回退、`--dry-run` 无副作用；
- CLI 覆盖参数与错误退出码。

运行 `--dry-run` 可对真实 AISBench 仓库做端到端渲染验证，不会产生任何文件。
