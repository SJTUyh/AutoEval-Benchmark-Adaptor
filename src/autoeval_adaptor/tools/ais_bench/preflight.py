"""AISBench 参数预检：校验适配参数与目标环境中安装的 AISBench/Harbor 能力是否匹配。"""

from __future__ import annotations

import re

from ...preflight import PreflightReport, ProbeError, run_probe

# build_model_from_cfg 中会在实例化前移除的配置级字段，不需要模型类 __init__ 接收。
MODEL_CONFIG_LEVEL_FIELDS = {
    "type",
    "attr",
    "abbr",
    "use_timestamp",
    "run_cfg",
    "request_rate",
    "batch_size",
    "response_anomaly",
    "summarizer_abbr",
    "pred_postprocessor",
    "min_out_len",
    "returns_tool_calls",
    "traffic_cfg",
}

_FLAG_PATTERN = re.compile(r"^--?[A-Za-z]")


BASE_TIMEOUT = 30.0
STAGE_TIMEOUT = 60.0


def build_report(prepared) -> PreflightReport:
    report = PreflightReport(tool=prepared.tool, subcommand=prepared.subcommand)
    spec = _build_spec(prepared)

    try:
        base = run_probe(
            prepared,
            {"datasets": spec["datasets"]},
            stage="base",
            timeout=BASE_TIMEOUT,
        )
    except ProbeError as exc:
        report.add("目标环境探测", "error", str(exc))
        return report

    if not _apply_base(report, prepared, base):
        return report

    if spec["modules"]:
        try:
            part = run_probe(
                prepared,
                {"modules": spec["modules"]},
                stage="modules",
                timeout=STAGE_TIMEOUT,
            )
        except ProbeError as exc:
            report.add("模块导入", "error", _stage_failure(exc))
        else:
            for item in part.get("modules") or []:
                _check_module(report, item)

    if spec["model"]:
        try:
            part = run_probe(
                prepared,
                {"model": spec["model"]},
                stage="model",
                timeout=STAGE_TIMEOUT,
            )
        except ProbeError as exc:
            report.add("模型参数", "error", _stage_failure(exc))
        else:
            _check_model(report, spec["model"], part.get("model"))

    if spec["harbor"]:
        try:
            part = run_probe(
                prepared,
                {"harbor": spec["harbor"]},
                stage="harbor",
                timeout=STAGE_TIMEOUT,
            )
        except ProbeError as exc:
            report.add("Harbor 环境", "error", _stage_failure(exc))
        else:
            _check_harbor(report, spec["harbor"], part.get("harbor"))

    return report


def evaluate_probe(report: PreflightReport, prepared, spec: dict, probe: dict) -> None:
    """把完整探测结果转换成预检条目。纯函数，便于单测。"""
    if not _apply_base(report, prepared, probe):
        return
    for item in probe.get("modules") or []:
        _check_module(report, item)
    _check_model(report, spec.get("model"), probe.get("model"))
    _check_harbor(report, spec.get("harbor"), probe.get("harbor"))


def _apply_base(report: PreflightReport, prepared, probe: dict) -> bool:
    python_info = probe.get("python") or {}
    if python_info:
        report.environment["python"] = (
            f"{python_info.get('version')} ({python_info.get('executable')})"
        )

    ais_bench = probe.get("ais_bench") or {}
    if not ais_bench.get("ok"):
        report.add(
            "AISBench 导入",
            "error",
            ais_bench.get("error") or "目标环境未安装 ais_bench",
        )
        return False
    report.environment["ais_bench"] = (
        f"{ais_bench.get('version') or 'unknown'} ({ais_bench.get('file')})"
    )
    report.add(
        "AISBench 导入",
        "pass",
        f"版本 {ais_bench.get('version') or 'unknown'}",
    )

    mmengine = probe.get("mmengine") or {}
    if mmengine.get("ok"):
        report.add("mmengine", "pass", f"版本 {mmengine.get('version') or 'unknown'}")
    else:
        report.add("mmengine", "warn", "未检测到 mmengine，AISBench 可能无法加载配置")

    for item in probe.get("datasets") or []:
        name = f"数据集配置 {item.get('module')}"
        if item.get("ok"):
            report.add(name, "pass", "模块与变量可导入")
        else:
            report.add(
                name,
                "error",
                f"{item.get('error') or '缺失'}（文件: {item.get('file')}）",
            )

    _check_cli(report, prepared, probe.get("cli") or {})
    return True


def _stage_failure(exc: Exception) -> str:
    return (
        f"{exc}；该阶段需要导入 AISBench/Harbor 的重型依赖，"
        "请先确认目标环境可正常导入，或使用 --skip-preflight 跳过预检"
    )


def _build_spec(prepared) -> dict:
    context = prepared.preflight_context or {}
    return {
        "modules": context.get("modules") or [],
        "datasets": context.get("datasets") or [],
        "model": context.get("model"),
        "harbor": context.get("harbor"),
    }


def _check_module(report: PreflightReport, item: dict) -> None:
    name = f"模块 {item.get('module')}"
    if item.get("ok"):
        report.add(name, "pass", "导入成功")
        return
    detail = item.get("error") or ""
    missing = item.get("missing") or []
    if missing:
        detail = f"缺少属性 {', '.join(missing)}" + (f"；{detail}" if detail else "")
    report.add(name, "error", detail or "导入失败")


def _check_cli(report: PreflightReport, prepared, cli: dict) -> None:
    if not cli.get("ok"):
        report.add("CLI 参数", "error", cli.get("error") or "无法解析 AISBench CLI")
        return

    options = set(cli.get("options") or [])
    flags = _collect_flags(prepared.command)
    unknown = sorted(flag for flag in flags if flag not in options)
    if unknown:
        report.add("CLI 参数", "error", f"当前 AISBench CLI 不支持: {', '.join(unknown)}")
    else:
        report.add("CLI 参数", "pass", f"{len(flags)} 个参数均被支持")

    mode = getattr(prepared, "mode", None)
    choices = cli.get("mode_choices") or []
    if mode:
        if choices and mode not in choices:
            report.add(
                "运行模式",
                "error",
                f"当前 AISBench 不支持 -m {mode}，可选: {', '.join(choices)}",
            )
        else:
            report.add("运行模式", "pass", f"-m {mode}")


def _check_model(report: PreflightReport, model_spec: dict | None, model: dict | None) -> None:
    if not model_spec:
        return
    if not model:
        report.add("模型参数", "error", "未返回模型探测结果")
        return
    if model.get("error"):
        report.add("模型参数", "error", model["error"])
        return

    class_name = model.get("class") or "<unknown>"
    config_errors = model.get("config_errors") or {}
    if config_errors:
        detail = "; ".join(f"{key}={value}" for key, value in config_errors.items())
        report.add("模型参数", "error", f"{class_name} 配置校验失败: {detail}")
    else:
        report.add("模型参数", "pass", f"{class_name} 配置校验通过")

    check_fields = model_spec.get("check_fields") or []
    init_params = set(model.get("init_params") or [])
    accepts_kwargs = bool(model.get("var_kwargs"))
    if check_fields and not accepts_kwargs:
        allowed = MODEL_CONFIG_LEVEL_FIELDS | init_params
        unknown = sorted(field for field in check_fields if field not in allowed)
        if unknown:
            report.add(
                "模型字段",
                "error",
                f"{class_name} 不接受字段: {', '.join(unknown)}",
            )
        else:
            report.add("模型字段", "pass", f"{len(check_fields)} 个字段均在支持范围内")


def _check_harbor(report: PreflightReport, harbor_spec: dict | None, harbor: dict | None) -> None:
    if not harbor_spec:
        return
    if not harbor:
        report.add("Harbor 环境", "error", "未返回 Harbor 探测结果")
        return
    if harbor.get("error"):
        report.add("Harbor 环境", "error", harbor["error"])
        return

    report.environment["harbor"] = harbor.get("version") or "unknown"

    missing = harbor.get("missing") or []
    for item in missing:
        detail = item.get("error") or ""
        if item.get("missing"):
            detail = f"缺少属性 {', '.join(item['missing'])}" + (
                f"；{detail}" if detail else ""
            )
        report.add(f"Harbor 模块 {item.get('module')}", "error", detail or "导入失败")
    if not missing:
        report.add("Harbor 运行时", "pass", "必需模块可导入")

    agent_ok = harbor.get("agent_ok")
    if agent_ok is False:
        report.add(
            "Harbor Agent",
            "error",
            f"当前 harbor 的 AgentName 不包含 {harbor_spec.get('agent_name')!r}",
        )
    elif agent_ok is True:
        report.add("Harbor Agent", "pass", str(harbor_spec.get("agent_name")))
    elif harbor.get("agent_error"):
        report.add("Harbor Agent", "warn", f"无法校验 AgentName: {harbor['agent_error']}")

    missing_params = harbor.get("source_missing") or []
    if missing_params:
        report.add(
            "Harbor 参数能力",
            "warn",
            "以下参数未在当前 AISBench 的 Harbor 实现中引用，可能被忽略: "
            + ", ".join(missing_params),
        )
    elif harbor_spec.get("tokens"):
        report.add("Harbor 参数能力", "pass", "参数均可被当前实现识别")

    for error in harbor.get("source_errors") or []:
        report.add("Harbor 源文件", "warn", f"无法读取用于能力检查的源文件: {error}")


def _collect_flags(command: list[str]) -> list[str]:
    return sorted({token for token in command if _FLAG_PATTERN.match(token)})
