"""通用参数预检：在目标 Python 环境中探测被调用工具的实际能力。

预检通过子进程运行探测脚本，因为适配器与测评工具可能不在同一个 Python 环境。
探测脚本只做导入和反射，不发起任何网络请求。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

SENTINEL = "__AUTOVAL_PREFLIGHT_RESULT__"


@dataclass
class CheckResult:
    name: str
    status: str  # pass / warn / error
    message: str

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "message": self.message}


@dataclass
class PreflightReport:
    tool: str
    subcommand: str
    checks: list[CheckResult] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[CheckResult]:
        return [check for check in self.checks if check.status == "error"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [check for check in self.checks if check.status == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, name: str, status: str, message: str) -> None:
        self.checks.append(CheckResult(name=name, status=status, message=message))

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "subcommand": self.subcommand,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "environment": self.environment,
        }


class ProbeError(RuntimeError):
    """探测脚本无法执行或未返回合法结果。"""


def run_probe(
    prepared, spec: dict, *, stage: str = "all", timeout: float = 180.0
) -> dict:
    """在目标解释器中执行指定阶段的探测脚本，返回其 JSON 结果。"""
    payload = dict(spec)
    payload["stage"] = stage
    command = [
        getattr(prepared, "python_executable", None) or sys.executable,
        "-c",
        PROBE_SCRIPT,
    ]
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            cwd=str(prepared.cwd),
            env=prepared.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except OSError as exc:
        raise ProbeError(f"无法启动目标解释器 {prepared.python_executable}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"预检探测超过 {timeout:g} 秒未完成") from exc

    output = completed.stdout or ""
    index = output.rfind(SENTINEL)
    if index < 0:
        detail = _summarize_output(completed.stderr or output)
        raise ProbeError(
            f"预检探测未返回结果（退出码 {completed.returncode}）: {detail}"
        )

    try:
        return json.loads(output[index + len(SENTINEL):])
    except json.JSONDecodeError as exc:
        raise ProbeError(f"预检探测结果不是合法 JSON: {exc}") from exc


def _summarize_output(text: str) -> str:
    """从探测进程输出中提取一行可读错误，避免把整段 traceback 塞进报告。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if "Error" in line or "Exception" in line or "error:" in line:
            return line[:500]
    return (lines[-1] if lines else "<无输出>")[:500]


def print_report(report: PreflightReport) -> None:
    print(f"[preflight] {report.tool}/{report.subcommand}")
    for key, value in report.environment.items():
        if value:
            print(f"[preflight] {key}: {value}")

    for check in report.checks:
        tag = {"pass": "PASS", "warn": "WARN", "error": "ERROR"}.get(
            check.status, check.status.upper()
        )
        print(f"  [{tag}] {check.name}: {check.message}")

    passes = sum(1 for check in report.checks if check.status == "pass")
    print(
        f"[preflight] 结果: {passes} passed, "
        f"{len(report.warnings)} warning(s), {len(report.errors)} error(s)"
    )


PROBE_SCRIPT = r'''
import ast
import contextlib
import copy
import importlib
import importlib.util
import inspect
import io
import json
import sys

SENTINEL = "__AUTOVAL_PREFLIGHT_RESULT__"


def module_file(name):
    try:
        spec = importlib.util.find_spec(name)
        return spec.origin if spec else None
    except BaseException:
        return None


def import_attrs(name, attrs):
    info = {"module": name, "ok": True, "file": module_file(name), "missing": [], "error": None}
    try:
        module = importlib.import_module(name)
    except BaseException as exc:
        info["ok"] = False
        info["missing"] = list(attrs)
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
        return info, None
    try:
        for attr in attrs:
            if not hasattr(module, attr):
                info["missing"].append(attr)
    except BaseException as exc:
        # 某些包的模块级 __getattr__ 会在懒加载时抛出非 AttributeError 异常。
        info["ok"] = False
        info["missing"] = list(attrs)
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
        return info, module
    info["ok"] = not info["missing"]
    return info, module


def assigned_names(path):
    try:
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
    except BaseException:
        return []
    names = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def probe_dataset(item):
    info = {"module": item["module"], "ok": False, "file": None, "variables": [], "error": None}
    try:
        path = module_file(item["module"])
        info["file"] = path
        if not path:
            info["error"] = "模块不存在"
            return info
        info["variables"] = assigned_names(path)
        expected = item.get("variable")
        info["ok"] = expected in info["variables"]
        if not info["ok"]:
            info["error"] = "未找到变量 %r" % (expected,)
    except BaseException as exc:
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def probe_model(spec):
    info = {
        "ok": False,
        "class": None,
        "init_params": [],
        "var_kwargs": False,
        "config_errors": {},
        "error": None,
    }
    try:
        module = importlib.import_module(spec["module"])
        base = getattr(module, spec["variable"])
        cfg = copy.deepcopy(base[0]) if isinstance(base, list) and base else {}
        cfg.update(spec.get("overrides") or {})
        cls = cfg.get("type")
        info["class"] = getattr(cls, "__name__", str(cls))
        try:
            signature = inspect.signature(cls.__init__)
            info["init_params"] = list(signature.parameters)
            info["var_kwargs"] = any(
                param.kind is inspect.Parameter.VAR_KEYWORD
                for param in signature.parameters.values()
            )
        except BaseException:
            pass
        try:
            from ais_bench.benchmark.utils.config.build import _validate_model_cfg

            info["config_errors"] = dict(_validate_model_cfg(cfg) or {})
        except BaseException:
            info["config_errors"] = {}
        info["ok"] = True
    except BaseException as exc:
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def probe_cli():
    info = {"ok": False, "options": [], "mode_choices": [], "error": None}
    try:
        from ais_bench.benchmark.cli.argument_parser import ArgumentParser

        parser = ArgumentParser().parser
        options = set()
        for action in parser._actions:
            options.update(action.option_strings)
        info["options"] = sorted(options)
        for action in parser._actions:
            if "-m" in action.option_strings or "--mode" in action.option_strings:
                info["mode_choices"] = list(action.choices or [])
        info["ok"] = True
    except BaseException as exc:
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def probe_harbor(spec):
    info = {
        "ok": False,
        "version": None,
        "missing": [],
        "agent_ok": None,
        "agent_error": None,
        "source_missing": [],
        "source_errors": [],
        "error": None,
    }
    try:
        try:
            from importlib.metadata import version

            info["version"] = version("harbor")
        except BaseException:
            try:
                import harbor as harbor_module

                info["version"] = getattr(harbor_module, "__version__", None)
            except BaseException:
                info["version"] = None

        for item in spec.get("modules") or []:
            module_info, _ = import_attrs(item["module"], item.get("attrs") or [])
            if not module_info["ok"]:
                info["missing"].append(module_info)

        agent_name = spec.get("agent_name")
        if agent_name and ":" not in agent_name:
            try:
                from harbor.models.agent.name import AgentName

                info["agent_ok"] = agent_name in AgentName.values()
            except BaseException as exc:
                info["agent_error"] = "%s: %s" % (type(exc).__name__, exc)

        tokens = spec.get("tokens") or []
        if tokens:
            sources = []
            for module_name in spec.get("sources") or []:
                try:
                    sources.append(inspect.getsource(importlib.import_module(module_name)))
                except BaseException as exc:
                    info["source_errors"].append(
                        "%s: %s" % (module_name, "%s: %s" % (type(exc).__name__, exc))
                    )
            source = "\n".join(sources)
            if source:
                info["source_missing"] = [
                    token
                    for token in tokens
                    if ('"%s"' % token) not in source and ("'%s'" % token) not in source
                ]
        info["ok"] = True
    except BaseException as exc:
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def main():
    raw = sys.stdin.read()
    spec = json.loads(raw) if raw.strip() else {}
    stage = spec.get("stage", "all")
    result = {
        "stage": stage,
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
    }

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        if stage in ("base", "all"):
            ais_bench = {"ok": False, "file": None, "version": None, "error": None}
            try:
                import ais_bench as ais_bench_module

                ais_bench["file"] = getattr(ais_bench_module, "__file__", None)
                ais_bench["version"] = getattr(ais_bench_module, "__version__", None)
                if not ais_bench["version"]:
                    try:
                        from ais_bench import benchmark as benchmark_module

                        ais_bench["version"] = getattr(
                            benchmark_module, "__version__", None
                        )
                    except BaseException:
                        pass
                ais_bench["ok"] = True
            except BaseException as exc:
                ais_bench["error"] = "%s: %s" % (type(exc).__name__, exc)
            result["ais_bench"] = ais_bench

            mmengine = {"ok": False, "version": None}
            if ais_bench["ok"]:
                try:
                    import mmengine as mmengine_module

                    mmengine["ok"] = True
                    mmengine["version"] = getattr(mmengine_module, "__version__", None)
                except BaseException:
                    pass
            result["mmengine"] = mmengine

            result["datasets"] = [
                probe_dataset(item) for item in spec.get("datasets") or []
            ]
            result["cli"] = probe_cli()

        if stage in ("modules", "all"):
            modules = []
            for item in spec.get("modules") or []:
                module_info, _ = import_attrs(item["module"], item.get("attrs") or [])
                modules.append(module_info)
            result["modules"] = modules

        if stage in ("model", "all"):
            result["model"] = probe_model(spec["model"]) if spec.get("model") else None

        if stage in ("harbor", "all"):
            result["harbor"] = (
                probe_harbor(spec["harbor"]) if spec.get("harbor") else None
            )

    sys.stdout.write(SENTINEL + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''
