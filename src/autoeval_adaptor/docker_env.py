"""按 (tool, subcommand) 管理内置 Docker 执行环境。

职责：
- 解析每个子命令对应的镜像名、容器名、Dockerfile 与隔离方式；
- 构建镜像、启动/停止容器、查询状态；
- 生成 ``ContainerRun``，并把它包装成 ``docker exec`` 命令。

适配器只在宿主编排，实际测评命令通过 ``docker exec`` 在容器内执行。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError, ExecutionError

DEFAULT_BENCHMARK_VERSION = "v3.1-20261013-master"
DEFAULT_OS_TAG = "ubuntu24.04-py312"
DEFAULT_BASE_REPO = "ghcr.io/aisbench/agent-runtime"
CONTAINER_WORKDIR = "/benchmark"
DOCKER_SOCK = "/var/run/docker.sock"
DIND_CASE_IMAGES = "/opt/agent-resources/images/case-images.tar"

# 适配器仓库根目录：src/autoeval_adaptor/docker_env.py -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER_ROOT = REPO_ROOT / "docker"

_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}

_ENVIRONMENT_META = {
    ("ais_bench", "llm_accuracy"): {
        "directory": "llm_accuracy",
        "isolation": "none",
        "python": "python3.12",
    },
    ("ais_bench", "harbor_agent"): {
        "directory": "harbor_agent",
        "isolation": "socket",
        "python": "/opt/venvs/harbor/bin/python",
    },
}

ISOLATION_KINDS = ("none", "socket", "dind")
EXECUTION_KINDS = ("auto", "docker", "local")


@dataclass(frozen=True)
class EnvSpec:
    """一个子命令对应的镜像/容器规格。"""

    tool: str
    subcommand: str
    dockerfile: Path
    build_context: Path
    image: str
    base_image: str
    container_name: str
    isolation: str
    python: str
    benchmark_version: str


@dataclass(frozen=True)
class Mount:
    host: str
    container: str
    readonly: bool = False

    def as_docker_arg(self) -> str:
        value = f"{self.host}:{self.container}"
        return value + ":ro" if self.readonly else value


@dataclass(frozen=True)
class ContainerRun:
    """已启动容器内执行一条命令所需的上下文。"""

    name: str
    image: str
    isolation: str
    workdir: str
    python: str
    env: dict[str, str] = field(default_factory=dict)
    mounts: tuple[Mount, ...] = ()


@dataclass(frozen=True)
class DockerOptions:
    """统一 JSON 中的 Docker 相关参数。"""

    execution: str = "auto"
    image: str | None = None
    base_image: str | None = None
    container_name: str | None = None
    mounts: list[str] = field(default_factory=list)
    env_isolation: str | None = None
    case_images: str | None = None
    datasets_dir: str | None = None
    benchmark_version: str | None = None
    keep_container: bool = True

    @classmethod
    def from_params(cls, params: dict, *, pop: bool = False) -> "DockerOptions":
        def take(key: str, default=None):
            if pop:
                return params.pop(key, default)
            return params.get(key, default)

        execution = take("execution", "auto") or "auto"
        if execution not in EXECUTION_KINDS:
            raise ConfigError(
                f"params.execution 取值必须是 {', '.join(EXECUTION_KINDS)} 之一，"
                f"实际为 {execution!r}"
            )
        isolation = take("env_isolation")
        if isolation is not None and isolation not in ISOLATION_KINDS:
            raise ConfigError(
                f"params.env_isolation 取值必须是 {', '.join(ISOLATION_KINDS)} 之一，"
                f"实际为 {isolation!r}"
            )
        mounts = take("mounts", []) or []
        if not isinstance(mounts, list) or not all(isinstance(item, str) for item in mounts):
            raise ConfigError("params.mounts 必须是字符串数组，格式 host:container[:ro]")
        keep_container = take("keep_container", True)
        if not isinstance(keep_container, bool):
            raise ConfigError("params.keep_container 必须是布尔值")
        return cls(
            execution=execution,
            image=take("docker_image") or None,
            base_image=take("base_image") or None,
            container_name=take("container_name") or None,
            mounts=list(mounts),
            env_isolation=isolation or None,
            case_images=take("case_images") or None,
            datasets_dir=take("datasets_dir") or None,
            benchmark_version=take("benchmark_version") or None,
            keep_container=keep_container,
        )


def normalize_arch() -> str:
    machine = platform.machine().lower()
    return _ARCH_ALIASES.get(machine, machine)


def docker_available() -> bool:
    """docker CLI 存在且 daemon 可访问。"""
    if not shutil.which("docker"):
        return False
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def docker_server_version(timeout: float = 20) -> str | None:
    try:
        completed = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value or None


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value.split("-")[0])
    return tuple(int(part) for part in parts[:3])


def verify_isolation(spec: EnvSpec) -> None:
    if spec.isolation != "dind":
        return
    version = docker_server_version()
    if not version:
        raise ExecutionError("无法读取 Docker 版本，不能确认是否支持 dind 模式")
    if _version_tuple(version) < (20, 10):
        raise ExecutionError(
            f"dind 模式要求 Docker >= 20.10（--cgroupns=host），当前 {version}；"
            "请改用默认 socket 模式或升级 Docker"
        )


def resolve_spec(tool: str, subcommand: str, docker: DockerOptions) -> EnvSpec:
    meta = _ENVIRONMENT_META.get((tool, subcommand))
    if meta is None:
        raise ConfigError(f"{tool}/{subcommand} 未定义内置 Docker 环境")

    version = docker.benchmark_version or DEFAULT_BENCHMARK_VERSION
    if not re.fullmatch(r"[0-9A-Za-z._-]+", version):
        raise ConfigError(f"params.benchmark_version 含非法字符: {version!r}")

    arch = normalize_arch()
    tag = f"{version}-{DEFAULT_OS_TAG}-{arch}"
    base_image = docker.base_image or f"{DEFAULT_BASE_REPO}:{tag}"
    image = docker.image or f"autoeval/ais_bench-{subcommand}:{tag}"
    container_name = docker.container_name or f"autoeval-{tool}-{subcommand}".replace("_", "-")
    isolation = docker.env_isolation or meta["isolation"]
    if isolation not in ISOLATION_KINDS:
        raise ConfigError(f"不支持的隔离方式: {isolation}")

    directory = DOCKER_ROOT / "ais_bench" / meta["directory"]
    return EnvSpec(
        tool=tool,
        subcommand=subcommand,
        dockerfile=directory / "Dockerfile",
        build_context=REPO_ROOT,
        image=image,
        base_image=base_image,
        container_name=container_name,
        isolation=isolation,
        python=meta["python"],
        benchmark_version=version,
    )


def _require_linux_host() -> None:
    if os.name == "nt":
        raise ConfigError(
            "docker 执行模式要求 Linux 宿主机（同路径挂载）；"
            "Windows 请使用 --local 或在 Linux 服务器上运行适配器"
        )


def _resolve_host_path(
    value: str | os.PathLike, *, what: str, validate: bool = True
) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    if validate and not path.exists():
        raise ConfigError(f"{what} 不存在: {path}")
    return str(path)


def parse_mounts(values: list[str], *, validate: bool = True) -> list[Mount]:
    mounts: list[Mount] = []
    for raw in values:
        parts = raw.split(":")
        if len(parts) not in (2, 3):
            raise ConfigError(f"非法挂载 {raw!r}，格式应为 host:container[:ro]")
        host, container = parts[0], parts[1]
        readonly = len(parts) == 3 and parts[2].lower() == "ro"
        if len(parts) == 3 and parts[2].lower() not in ("ro", "rw"):
            raise ConfigError(f"非法挂载模式 {parts[2]!r}，只支持 ro/rw")
        if not host or not container:
            raise ConfigError(f"非法挂载 {raw!r}，host 与 container 均不能为空")
        host_path = _resolve_host_path(host, what="挂载源目录", validate=validate)
        mounts.append(Mount(host_path, container, readonly))
    return mounts


def _collect_mounts(
    spec: EnvSpec, params: dict, docker: DockerOptions, *, validate: bool = True
) -> list[Mount]:
    _require_linux_host()
    mounts: list[Mount] = []

    def add(host: str, container: str, readonly: bool = False) -> None:
        resolved = _resolve_host_path(host, what="挂载源目录", validate=validate)
        if spec.isolation == "socket" and resolved != os.path.abspath(host):
            raise ConfigError(
                f"socket 模式要求真实绝对路径（不能含符号链接）: {host} -> {resolved}"
            )
        mounts.append(Mount(resolved, container, readonly))

    work_dir = params.get("work_dir")
    if work_dir:
        resolved_work_dir = _resolve_host_path(
            work_dir, what="work_dir", validate=validate
        )
        add(resolved_work_dir, resolved_work_dir)

    for mount in parse_mounts(docker.mounts, validate=validate):
        mounts.append(mount)

    if spec.subcommand == "llm_accuracy" and docker.datasets_dir:
        add(docker.datasets_dir, f"{CONTAINER_WORKDIR}/ais_bench/datasets", readonly=True)

    if spec.subcommand == "harbor_agent":
        dataset_path = params.get("path")
        if dataset_path:
            add(str(dataset_path), str(dataset_path))
        deps_path = params.get("deps_path")
        if deps_path:
            add(str(deps_path), str(deps_path))
        if spec.isolation == "dind" and docker.case_images:
            add(docker.case_images, DIND_CASE_IMAGES, readonly=True)

    return _dedupe_mounts(mounts)


def _dedupe_mounts(mounts: list[Mount]) -> list[Mount]:
    result: dict[str, Mount] = {}
    for mount in mounts:
        existing = result.get(mount.container)
        if existing and existing.host != mount.host:
            raise ConfigError(
                f"挂载冲突: {mount.container} 同时映射到 {existing.host} 和 {mount.host}"
            )
        result[mount.container] = mount
    return list(result.values())


def _run(
    command: list[str],
    *,
    capture: bool = True,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            command,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except OSError as exc:
        raise ExecutionError(f"无法执行 docker: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(f"docker 命令超时: {' '.join(command)}") from exc
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ExecutionError(
            f"docker 命令失败（退出码 {completed.returncode}）: "
            f"{' '.join(command)}\n{detail[-2000:]}"
        )
    return completed


def image_exists(image: str) -> bool:
    completed = _run(["docker", "image", "inspect", image], check=False)
    return completed.returncode == 0


def build_image(spec: EnvSpec, *, force: bool = False) -> None:
    if not spec.dockerfile.is_file():
        raise ExecutionError(f"缺少 Dockerfile: {spec.dockerfile}")
    command = ["docker", "build", "--network", "host"]
    if force:
        command.append("--no-cache")
    command += [
        "--build-arg",
        f"BASE_IMAGE={spec.base_image}",
        "--build-arg",
        f"BENCHMARK_VERSION={spec.benchmark_version}",
        "-f",
        str(spec.dockerfile),
        "-t",
        spec.image,
        str(spec.build_context),
    ]
    print(f"[env] 构建镜像: {spec.image}", file=sys.stdout)
    print(f"[env] 基镜像: {spec.base_image}", file=sys.stdout)
    print(f"[env] 命令: {' '.join(command)}", file=sys.stdout)
    completed = subprocess.run(command)
    if completed.returncode != 0:
        raise ExecutionError(f"镜像构建失败（退出码 {completed.returncode}）: {spec.image}")


def container_state(name: str) -> str:
    completed = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    if completed.returncode != 0:
        return "absent"
    return "running" if (completed.stdout or "").strip().lower() == "true" else "exited"


def container_isolation(name: str) -> str | None:
    completed = _run(
        [
            "docker",
            "inspect",
            "-f",
            '{{index .Config.Labels "autoeval.isolation"}}',
            name,
        ],
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value or None


def _run_container(spec: EnvSpec, params: dict, docker: DockerOptions) -> None:
    mounts = _collect_mounts(spec, params, docker)
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        spec.container_name,
        "--label",
        f"autoeval.isolation={spec.isolation}",
        "--label",
        f"autoeval.subcommand={spec.subcommand}",
        "--net=host",
        "--ipc=host",
        "-w",
        CONTAINER_WORKDIR,
    ]
    for mount in mounts:
        command += ["-v", mount.as_docker_arg()]
    if spec.isolation == "socket":
        command += ["-v", f"{DOCKER_SOCK}:/var/run/docker.sock"]
    elif spec.isolation == "dind":
        verify_isolation(spec)
        command += [
            "--privileged",
            "--cgroupns=host",
            "-v",
            "/sys/fs/cgroup:/sys/fs/cgroup:rw",
        ]
    command += [spec.image, "sleep", "infinity"]

    print(f"[env] 启动容器: {spec.container_name}（{spec.isolation}）", file=sys.stdout)
    _run(command)
    _after_start(spec, docker)


def _after_start(spec: EnvSpec, docker: DockerOptions) -> None:
    if spec.isolation == "socket":
        completed = _run(
            ["docker", "exec", spec.container_name, "docker", "info"],
            check=False,
            timeout=60,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ExecutionError(
                "容器内 docker 不可用（socket 挂载失败？）"
                + (f"\n{detail[-1000:]}" if detail else "")
            )
        return

    if spec.isolation != "dind":
        return

    _run(
        [
            "docker",
            "exec",
            "-d",
            spec.container_name,
            "bash",
            "-c",
            "nohup dockerd > /tmp/dockerd.log 2>&1 &",
        ]
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        completed = _run(
            ["docker", "exec", spec.container_name, "docker", "info"],
            check=False,
            timeout=20,
        )
        if completed.returncode == 0:
            break
        time.sleep(3)
    else:
        raise ExecutionError(
            "容器内 dockerd 180 秒未就绪，请检查 /tmp/dockerd.log"
        )
    if docker.case_images:
        _run(
            [
                "docker",
                "exec",
                spec.container_name,
                "docker",
                "load",
                "-i",
                DIND_CASE_IMAGES,
            ]
        )


def ensure_container(spec: EnvSpec, params: dict, docker: DockerOptions) -> None:
    if not docker_available():
        raise ExecutionError("docker CLI 或 daemon 不可用，无法启动执行环境")
    if spec.isolation == "dind":
        verify_isolation(spec)

    work_dir = params.get("work_dir")
    if work_dir:
        Path(work_dir).expanduser().mkdir(parents=True, exist_ok=True)

    state = container_state(spec.container_name)
    if state == "running":
        actual = container_isolation(spec.container_name)
        if actual != spec.isolation and (actual is not None or spec.isolation == "dind"):
            raise ExecutionError(
                f"容器 {spec.container_name} 当前隔离模式为 {actual or 'unknown'}，"
                f"与请求的 {spec.isolation} 不一致；请先执行 --stop-env"
            )
        print(f"[env] 复用运行中的容器: {spec.container_name}", file=sys.stdout)
        if spec.isolation == "socket":
            _after_start(spec, docker)
        return
    if state == "exited":
        _run(["docker", "rm", "-f", spec.container_name])
    if not image_exists(spec.image):
        build_image(spec)
    _run_container(spec, params, docker)


def start_container(spec: EnvSpec, params: dict, docker: DockerOptions) -> None:
    ensure_container(spec, params, docker)


def stop_container(spec: EnvSpec) -> None:
    if container_state(spec.container_name) == "absent":
        print(f"[env] 容器不存在: {spec.container_name}", file=sys.stdout)
        return
    _run(["docker", "rm", "-f", spec.container_name])
    print(f"[env] 已删除容器: {spec.container_name}", file=sys.stdout)


def status(spec: EnvSpec, params: dict, docker: DockerOptions) -> dict:
    has_image = image_exists(spec.image)
    state = container_state(spec.container_name)
    info = {
        "tool": spec.tool,
        "subcommand": spec.subcommand,
        "image": spec.image,
        "image_present": has_image,
        "base_image": spec.base_image,
        "container": spec.container_name,
        "container_state": state,
        "container_isolation": container_isolation(spec.container_name),
        "isolation": spec.isolation,
        "python": spec.python,
        "workdir": CONTAINER_WORKDIR,
        "mounts": [
            mount.as_docker_arg()
            for mount in _collect_mounts(spec, params, docker, validate=False)
        ],
    }
    if state == "running":
        completed = _run(
            [
                "docker",
                "exec",
                spec.container_name,
                spec.python,
                "-c",
                (
                    "import importlib.metadata as m, sys\n"
                    "print('python', sys.version.split()[0])\n"
                    "try:\n"
                    "    print('ais_bench_benchmark', m.version('ais_bench_benchmark'))\n"
                    "except Exception as exc:\n"
                    "    print('ais_bench_benchmark', 'unknown', exc)\n"
                    "try:\n"
                    "    print('harbor', m.version('harbor'))\n"
                    "except Exception:\n"
                    "    pass\n"
                ),
            ],
            check=False,
            timeout=60,
        )
        info["runtime"] = {
            line.split(" ", 1)[0]: line.split(" ", 1)[1]
            for line in (completed.stdout or "").splitlines()
            if " " in line
        }
    return info


def container_run(
    spec: EnvSpec, params: dict, docker: DockerOptions, *, validate: bool = True
) -> ContainerRun:
    return ContainerRun(
        name=spec.container_name,
        image=spec.image,
        isolation=spec.isolation,
        workdir=CONTAINER_WORKDIR,
        python=spec.python,
        env={"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
        mounts=tuple(_collect_mounts(spec, params, docker, validate=validate)),
    )


def wrap_exec(container: ContainerRun, argv: list[str]) -> list[str]:
    command = ["docker", "exec", "-i", "-w", container.workdir]
    for key, value in container.env.items():
        command += ["-e", f"{key}={value}"]
    command.append(container.name)
    command.extend(argv)
    return command
