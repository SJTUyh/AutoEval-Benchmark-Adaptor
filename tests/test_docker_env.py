import pytest

from autoeval_adaptor import docker_env
from autoeval_adaptor.docker_env import (
    ContainerRun,
    DockerOptions,
    Mount,
    container_run,
    normalize_arch,
    normalize_benchmark_source,
    parse_mounts,
    resolve_spec,
    verify_isolation,
    wrap_exec,
)
from autoeval_adaptor.errors import ConfigError, ExecutionError


def test_normalize_arch_maps_common_names(monkeypatch):
    monkeypatch.setattr(docker_env.platform, "machine", lambda: "AMD64")
    assert normalize_arch() == "x86_64"
    monkeypatch.setattr(docker_env.platform, "machine", lambda: "arm64")
    assert normalize_arch() == "aarch64"
    monkeypatch.setattr(docker_env.platform, "machine", lambda: "x86_64")
    assert normalize_arch() == "x86_64"


def test_resolve_spec_for_each_subcommand():
    accuracy = resolve_spec("ais_bench", "llm_accuracy", DockerOptions())
    assert accuracy.image.startswith("autoeval/ais_bench-llm_accuracy:")
    assert accuracy.base_image.startswith(
        "ghcr.io/aisbench/agent-runtime:v3.1-20261013-master-"
    )
    assert accuracy.isolation == "none"
    assert accuracy.python == "python3.12"
    assert accuracy.container_name == "autoeval-ais-bench-llm-accuracy"
    assert accuracy.dockerfile.name == "Dockerfile"

    harbor = resolve_spec("ais_bench", "harbor_agent", DockerOptions())
    assert harbor.isolation == "socket"
    assert harbor.python == "/opt/venvs/harbor/bin/python"
    assert harbor.container_name == "autoeval-ais-bench-harbor-agent"


def test_resolve_spec_honors_overrides():
    spec = resolve_spec(
        "ais_bench",
        "harbor_agent",
        DockerOptions(
            image="my/image:1",
            base_image="base:1",
            container_name="c1",
            env_isolation="dind",
            benchmark_version="v9.9-test",
        ),
    )
    assert spec.image == "my/image:1"
    assert spec.base_image == "base:1"
    assert spec.container_name == "c1"
    assert spec.isolation == "dind"
    assert spec.benchmark_version == "v9.9-test"


def test_resolve_spec_rejects_unknown_environment():
    with pytest.raises(ConfigError):
        resolve_spec("other", "x", DockerOptions())


def test_normalize_benchmark_source_from_tree_url():
    repo, ref = normalize_benchmark_source(
        "https://github.com/SJTUyh/benchmark/tree/autoeval", None
    )
    assert repo == "https://github.com/SJTUyh/benchmark.git"
    assert ref == "autoeval"


def test_normalize_benchmark_source_repo_plus_ref():
    repo, ref = normalize_benchmark_source(
        "https://github.com/SJTUyh/benchmark", "feature/foo"
    )
    assert repo == "https://github.com/SJTUyh/benchmark.git"
    assert ref == "feature/foo"


def test_normalize_benchmark_source_requires_ref():
    with pytest.raises(ConfigError, match="分支或 tag"):
        normalize_benchmark_source("https://github.com/SJTUyh/benchmark.git", None)
    with pytest.raises(ConfigError, match="benchmark_repo"):
        normalize_benchmark_source(None, "autoeval")
    assert normalize_benchmark_source(None, None) == (None, None)


def test_resolve_spec_with_personal_branch():
    spec = resolve_spec(
        "ais_bench",
        "llm_accuracy",
        DockerOptions(
            benchmark_repo="https://github.com/SJTUyh/benchmark/tree/autoeval"
        ),
    )
    assert spec.benchmark_repo == "https://github.com/SJTUyh/benchmark.git"
    assert spec.benchmark_ref == "autoeval"
    assert spec.source_label == "SJTUyh-autoeval"
    assert spec.image.endswith("-SJTUyh-autoeval")
    assert spec.container_name == "autoeval-ais-bench-llm-accuracy-sjtuyh-autoeval"
    # 基镜像仍使用官方 release tag，不追加个人分支后缀
    assert spec.base_image.endswith(
        "v3.1-20261013-master-ubuntu24.04-py312-x86_64"
    )


def test_parse_mounts_rejects_bad_format():
    for raw in ("hostonly", "a:b:c:d", "h:/c:rx", ":/c"):
        with pytest.raises(ConfigError):
            parse_mounts([raw])


def test_parse_mounts_marks_readonly(monkeypatch):
    monkeypatch.setattr(
        docker_env, "_resolve_host_path", lambda value, **kwargs: str(value)
    )
    mounts = parse_mounts(["/host/data:/data:ro", "/x:/y"])
    assert mounts == [
        Mount("/host/data", "/data", True),
        Mount("/x", "/y", False),
    ]


def test_verify_isolation_requires_docker_20_10(monkeypatch):
    spec = resolve_spec(
        "ais_bench", "harbor_agent", DockerOptions(env_isolation="dind")
    )
    monkeypatch.setattr(docker_env, "docker_server_version", lambda: "18.09.0")
    with pytest.raises(ExecutionError, match="20.10"):
        verify_isolation(spec)

    monkeypatch.setattr(docker_env, "docker_server_version", lambda: "27.3.1")
    verify_isolation(spec)


def test_wrap_exec_builds_docker_exec_command():
    container = ContainerRun(
        name="c1",
        image="img:tag",
        isolation="socket",
        workdir="/benchmark",
        python="python3.12",
        env={"PYTHONUNBUFFERED": "1"},
    )
    command = wrap_exec(container, ["python3.12", "-m", "ais_bench.benchmark.cli.main"])
    assert command == [
        "docker",
        "exec",
        "-i",
        "-w",
        "/benchmark",
        "-e",
        "PYTHONUNBUFFERED=1",
        "c1",
        "python3.12",
        "-m",
        "ais_bench.benchmark.cli.main",
    ]


def test_docker_options_from_params_validates():
    with pytest.raises(ConfigError):
        DockerOptions.from_params({"execution": "bad"})
    with pytest.raises(ConfigError):
        DockerOptions.from_params({"mounts": "not-a-list"})
    with pytest.raises(ConfigError):
        DockerOptions.from_params({"keep_container": "yes"})

    options = DockerOptions.from_params(
        {"execution": "local", "mounts": ["/a:/b"], "keep_container": False}
    )
    assert options.execution == "local"
    assert options.keep_container is False


def test_container_run_collects_subcommand_mounts(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_env, "_require_linux_host", lambda: None)
    work_dir = tmp_path / "run"
    dataset = tmp_path / "tasks"
    deps = tmp_path / "deps"
    work_dir.mkdir()
    dataset.mkdir()
    deps.mkdir()

    harbor = resolve_spec("ais_bench", "harbor_agent", DockerOptions())
    runtime = container_run(
        harbor,
        {
            "work_dir": str(work_dir),
            "path": str(dataset),
            "deps_path": str(deps),
        },
        DockerOptions(),
    )
    containers = {mount.container for mount in runtime.mounts}
    assert str(work_dir) in containers
    assert str(dataset) in containers
    assert str(deps) in containers

    accuracy = resolve_spec("ais_bench", "llm_accuracy", DockerOptions())
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    runtime = container_run(
        accuracy,
        {"work_dir": str(work_dir)},
        DockerOptions(datasets_dir=str(datasets_dir)),
    )
    assert "/benchmark/ais_bench/datasets" in {
        mount.container for mount in runtime.mounts
    }


def test_ensure_container_reuses_running_container(tmp_path, monkeypatch):
    work_dir = tmp_path / "run"
    spec = resolve_spec("ais_bench", "llm_accuracy", DockerOptions())
    params = {"work_dir": str(work_dir)}
    docker = DockerOptions()

    monkeypatch.setattr(docker_env, "docker_available", lambda: True)
    monkeypatch.setattr(docker_env, "container_state", lambda name: "running")
    monkeypatch.setattr(docker_env, "container_isolation", lambda name: "none")
    monkeypatch.setattr(docker_env, "_after_start", lambda spec, docker: None)
    monkeypatch.setattr(
        docker_env, "build_image", lambda *args, **kwargs: pytest.fail("不应构建镜像")
    )
    monkeypatch.setattr(
        docker_env, "_run_container", lambda *args, **kwargs: pytest.fail("不应启动容器")
    )

    docker_env.ensure_container(spec, params, docker)
    assert work_dir.is_dir()


def test_ensure_container_rejects_isolation_mismatch(tmp_path, monkeypatch):
    spec = resolve_spec("ais_bench", "harbor_agent", DockerOptions())
    params = {"work_dir": str(tmp_path / "run")}
    monkeypatch.setattr(docker_env, "docker_available", lambda: True)
    monkeypatch.setattr(docker_env, "container_state", lambda name: "running")
    monkeypatch.setattr(docker_env, "container_isolation", lambda name: "dind")

    with pytest.raises(ExecutionError, match="不一致"):
        docker_env.ensure_container(spec, params, DockerOptions())


def test_ensure_container_dind_version_gate(tmp_path, monkeypatch):
    spec = resolve_spec(
        "ais_bench", "harbor_agent", DockerOptions(env_isolation="dind")
    )
    monkeypatch.setattr(docker_env, "docker_available", lambda: True)
    monkeypatch.setattr(docker_env, "docker_server_version", lambda: "18.09.0")

    with pytest.raises(ExecutionError, match="20.10"):
        docker_env.ensure_container(
            spec, {"work_dir": str(tmp_path / "run")}, DockerOptions(env_isolation="dind")
        )
