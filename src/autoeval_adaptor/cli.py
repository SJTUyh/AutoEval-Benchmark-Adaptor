"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Request, load_request
from .docker_env import (
    DockerOptions,
    build_image,
    container_run,
    docker_available,
    ensure_container,
    resolve_spec,
    status,
    stop_container,
)
from .errors import AdaptorError, ConfigError
from .preflight import print_report
from .registry import get_adapter
from .runner import execute
from .template import load_template, merge_params


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoeval-adaptor",
        description="用统一 JSON 配置调用不同测评工具",
    )
    parser.add_argument("--config", required=True, help="统一 JSON 配置文件路径")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印生成的配置与执行命令，不创建目录、不执行",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="只执行参数预检并打印结果，不运行测评",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="跳过执行前的参数预检（--dry-run 默认跳过）",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="强制在本机执行，不使用 Docker 容器",
    )
    parser.add_argument(
        "--build-env",
        action="store_true",
        help="构建当前 tool/subcommand 对应的 Docker 镜像",
    )
    parser.add_argument(
        "--start-env",
        action="store_true",
        help="构建（镜像缺失时）并启动 Docker 容器",
    )
    parser.add_argument(
        "--stop-env",
        action="store_true",
        help="停止并删除当前 tool/subcommand 的 Docker 容器",
    )
    parser.add_argument(
        "--env-status",
        action="store_true",
        help="打印镜像、容器与环境版本状态",
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="配合 --build-env 使用 --no-cache 重新构建",
    )
    parser.add_argument(
        "--env-isolation",
        choices=("socket", "dind"),
        help="覆盖 params.env_isolation（仅 harbor_agent 有效）",
    )
    parser.add_argument(
        "--benchmark-source",
        help=(
            "使用个人仓/分支的 benchmark 源码构建镜像，"
            "支持 https://github.com/<owner>/<repo>/tree/<ref> 或仓库 URL"
        ),
    )
    parser.add_argument(
        "--benchmark-ref",
        help="benchmark 仓库分支或 tag；使用完整 tree URL 时可省略",
    )
    parser.add_argument("--work-dir", help="覆盖 params.work_dir")
    parser.add_argument("--ais-bench-root", help="覆盖 params.ais_bench_root")
    parser.add_argument(
        "--template",
        metavar="PATH",
        help="使用指定的配置模板文件，替代内置模板",
    )
    parser.add_argument(
        "--no-template",
        action="store_true",
        help="不使用内置配置模板，直接使用配置文件中的参数",
    )
    parser.add_argument(
        "--show-template",
        action="store_true",
        help="打印当前 tool/subcommand 使用的配置模板后退出",
    )
    parser.add_argument(
        "--python",
        dest="python_executable",
        help="覆盖 params.python_executable（本机执行时运行 AISBench 的解释器）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        request = load_request(args.config)
        adapter = get_adapter(request.tool, request.subcommand)

        if args.template and args.no_template:
            raise ConfigError("--template 与 --no-template 不能同时使用")

        if args.show_template:
            loaded = load_template(request.tool, request.subcommand, args.template)
            print(
                json.dumps(
                    {
                        "tool": request.tool,
                        "subcommand": request.subcommand,
                        "params": loaded.params,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        template_source = None
        if not args.no_template:
            loaded = load_template(request.tool, request.subcommand, args.template)
            request = Request(
                tool=request.tool,
                subcommand=request.subcommand,
                params=merge_params(loaded.params, request.params),
            )
            template_source = loaded.source
            print(f"[adaptor] 使用配置模板: {template_source}")

        _apply_overrides(request.params, args)

        docker = DockerOptions.from_params(request.params)
        spec = resolve_spec(request.tool, request.subcommand, docker)

        if args.stop_env:
            stop_container(spec)
            return 0
        if args.env_status:
            print(
                json.dumps(
                    status(spec, request.params, docker),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.build_env:
            build_image(spec, force=args.force_rebuild)
            if not args.start_env:
                return 0
        if args.start_env:
            ensure_container(spec, request.params, docker)
            return 0

        execution = _resolve_execution(args, docker)
        container = None
        if execution == "docker":
            if not args.dry_run:
                ensure_container(spec, request.params, docker)
            container = container_run(
                spec, request.params, docker, validate=not args.dry_run
            )

        prepared = adapter.prepare(request.params, container=container)
        prepared.summary["template"] = template_source

        preflight_payload = None
        if args.preflight_only or (not args.skip_preflight and not args.dry_run):
            report = adapter.preflight(prepared)
            print_report(report)
            preflight_payload = report.to_dict()
            if args.preflight_only:
                return 0 if report.ok else 2
            if not report.ok:
                print("[adaptor] 预检未通过，已阻止执行", file=sys.stderr)
                return 2

        result = execute(prepared, dry_run=args.dry_run, preflight=preflight_payload)
    except AdaptorError as exc:
        print(f"[adaptor] 错误: {exc}", file=sys.stderr)
        return exc.exit_code

    if result["status"] in ("success", "dry_run"):
        return 0
    return int(result.get("exit_code") or 1)


def _resolve_execution(args: argparse.Namespace, docker: DockerOptions) -> str:
    if args.local:
        return "local"
    if docker.execution != "auto":
        return docker.execution
    if docker_available():
        return "docker"
    print(
        "[adaptor] 未检测到可用的 Docker，回退本机执行；"
        "如需容器执行请先启动 docker（或显式传 --local）",
        file=sys.stderr,
    )
    return "local"


def _apply_overrides(params: dict, args: argparse.Namespace) -> None:
    if args.work_dir:
        params["work_dir"] = args.work_dir
    if args.ais_bench_root:
        params["ais_bench_root"] = args.ais_bench_root
    if args.python_executable:
        params["python_executable"] = args.python_executable
    if args.env_isolation:
        params["env_isolation"] = args.env_isolation
    if args.benchmark_source:
        params["benchmark_repo"] = args.benchmark_source
    if args.benchmark_ref:
        params["benchmark_ref"] = args.benchmark_ref
