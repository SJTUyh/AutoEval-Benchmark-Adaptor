"""命令行入口。"""

from __future__ import annotations

import argparse
import sys

from .collector import collect_results
from .config import load_request
from .errors import AdaptorError, ConfigError
from .preflight import print_report
from .registry import get_adapter
from .runner import execute


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoeval-adaptor",
        description="用统一 JSON 配置调用不同测评工具",
    )
    parser.add_argument("--config", required=True, help="统一 JSON 配置文件路径")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印生成的配置与执行命令，不创建目录、不执行",
    )
    mode.add_argument(
        "--preflight-only",
        action="store_true",
        help="只执行参数预检并打印结果，不运行测评",
    )
    mode.add_argument(
        "--collect-results",
        action="store_true",
        help="只从已有测评产物提取统一结果，不执行测评、不做预检",
    )
    parser.add_argument(
        "--result-dir",
        help="已有测评产物目录；默认读取 adaptor_result.json 或最新时间戳目录",
    )
    parser.add_argument(
        "--output",
        help="统一结果输出路径，默认 <work_dir>/unified_result.json",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="跳过执行前的参数预检（--dry-run 默认跳过）",
    )
    parser.add_argument("--work-dir", help="覆盖 params.work_dir")
    parser.add_argument("--ais-bench-root", help="覆盖 params.ais_bench_root")
    parser.add_argument(
        "--python",
        dest="python_executable",
        help="覆盖 params.python_executable（运行 AISBench 的解释器）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if not args.collect_results and (args.result_dir or args.output):
            raise ConfigError(
                "--result-dir / --output 只能与 --collect-results 一起使用"
            )

        request = load_request(args.config)
        _apply_overrides(request.params, args)
        adapter = get_adapter(request.tool, request.subcommand)
        prepared = adapter.prepare(request.params)

        if args.collect_results:
            outcome = collect_results(
                prepared,
                adapter,
                result_dir=args.result_dir,
                output=args.output,
            )
            payload = outcome["result"]
            print(
                "[collect] "
                f"total_score={payload.get('total_score')} "
                f"cases={payload.get('case_count')} "
                f"result={outcome['path']}"
            )
            return 0

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

        result = execute(
            prepared,
            dry_run=args.dry_run,
            preflight=preflight_payload,
            adapter=adapter,
        )
    except AdaptorError as exc:
        print(f"[adaptor] 错误: {exc}", file=sys.stderr)
        return exc.exit_code

    if result["status"] in ("success", "dry_run"):
        return 0
    return int(result.get("exit_code") or 1)


def _apply_overrides(params: dict, args: argparse.Namespace) -> None:
    if args.work_dir:
        params["work_dir"] = args.work_dir
    if args.ais_bench_root:
        params["ais_bench_root"] = args.ais_bench_root
    if args.python_executable:
        params["python_executable"] = args.python_executable
