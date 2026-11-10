"""定位 AISBench 根目录并解析模型/数据集配置别名。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .errors import DiscoveryError

MARKER = Path("ais_bench") / "benchmark" / "cli" / "main.py"

# 当前 llm_accuracy 只支持服务化模型配置；本地/HF 模型的推理任务不同，需另行适配。
SERVICE_MODEL_CATEGORIES = ("vllm_api", "mindie_api", "tgi_api", "triton_api")


@dataclass(frozen=True)
class ConfigRef:
    module: str
    variable: str
    path: Path


def resolve_root(explicit: str | Path | None, search_from: Path | None = None) -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not (root / MARKER).is_file():
            raise DiscoveryError(
                f"AISBench 根目录不合法: {root}，需包含 {MARKER.as_posix()}"
            )
        return root

    start = Path(search_from or Path.cwd()).resolve()
    project_root = Path(__file__).resolve().parents[2]
    seen: set[Path] = set()
    for anchor in (start, project_root):
        for candidate in (
            anchor / "benchmark",
            anchor,
            anchor.parent / "benchmark",
        ):
            root = candidate.resolve()
            if root in seen:
                continue
            seen.add(root)
            if (root / MARKER).is_file():
                return root

    searched = ", ".join(str(path) for path in sorted(seen))
    raise DiscoveryError(
        "未找到 AISBench 根目录，请通过 params.ais_bench_root 或 --ais-bench-root 指定；"
        f"已尝试: {searched}"
    )


def resolve_model(root: Path, alias: str) -> ConfigRef:
    name = _normalize_alias(alias, "model_type")
    match = name.rsplit("/", 1)[-1]
    files = _find_files(_model_dirs(root), match)
    if not files:
        raise DiscoveryError(
            f"未找到模型配置 {alias!r}，请确认 {root} 下的 configs/models 中存在 {match}.py"
        )
    if len(files) > 1:
        raise DiscoveryError(
            f"模型配置别名 {alias!r} 命中多个文件，请改用更精确的别名: "
            + ", ".join(_relative(root, path) for path in files)
        )

    path = files[0]
    category = _category_after(path, "models")
    if category not in SERVICE_MODEL_CATEGORIES:
        raise DiscoveryError(
            f"模型配置 {alias!r} 属于 {category or '未知'} 目录，"
            f"llm_accuracy 仅支持服务化模型配置: {', '.join(SERVICE_MODEL_CATEGORIES)}"
        )

    variable = _pick_variable(path, model=True)
    return ConfigRef(module=_module_name(root, path), variable=variable, path=path)


def resolve_dataset(root: Path, alias: str) -> ConfigRef:
    name = _normalize_alias(alias, "datasets")
    match = name.rsplit("/", 1)[-1]
    files = _find_files(_dataset_dirs(root), match)
    if not files:
        raise DiscoveryError(
            f"未找到数据集配置 {alias!r}，请确认 {root} 下的 configs/datasets 中存在 {match}.py"
        )
    if len(files) > 1:
        raise DiscoveryError(
            f"数据集配置别名 {alias!r} 命中多个文件，请改用更精确的别名: "
            + ", ".join(_relative(root, path) for path in files)
        )

    path = files[0]
    variable = _pick_variable(path, model=False)
    return ConfigRef(module=_module_name(root, path), variable=variable, path=path)


def _normalize_alias(alias: str, field_name: str) -> str:
    value = alias.strip()
    if value.endswith(".py"):
        value = value[:-3]
    if not value:
        raise DiscoveryError(f"params.{field_name} 中存在空别名")
    return value


def _model_dirs(root: Path) -> list[Path]:
    return [
        root / "ais_bench" / "benchmark" / "configs" / "models",
        root / "configs" / "models",
    ]


def _dataset_dirs(root: Path) -> list[Path]:
    return [
        root / "ais_bench" / "benchmark" / "configs" / "datasets",
        root / "ais_bench" / "benchmark" / "configs" / "dataset_collections",
        root / "configs" / "datasets",
        root / "configs" / "dataset_collections",
    ]


def _find_files(dirs: list[Path], stem: str) -> list[Path]:
    found: list[Path] = []
    for base in dirs:
        if not base.is_dir():
            continue
        for path in base.rglob(f"{stem}.py"):
            if "__pycache__" in path.parts:
                continue
            found.append(path)
    return sorted(found)


def _pick_variable(path: Path, *, model: bool) -> str:
    names = _assigned_names(path)
    if model:
        if "models" in names:
            return "models"
        for name in names:
            if name.endswith("_models"):
                return name
        raise DiscoveryError(f"模型配置文件 {path} 中未找到 models 变量")

    for name in names:
        if name.endswith("_datasets"):
            return name
    raise DiscoveryError(f"数据集配置文件 {path} 中未找到以 _datasets 结尾的变量")


def _assigned_names(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise DiscoveryError(f"无法解析配置文件 {path}: {exc}") from exc

    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def _module_name(root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    return ".".join(relative.with_suffix("").parts)


def _category_after(path: Path, parent: str) -> str:
    parts = path.parts
    try:
        index = parts.index(parent)
    except ValueError:
        return ""
    if index + 2 <= len(parts) - 1:
        return parts[index + 1]
    return ""


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)
