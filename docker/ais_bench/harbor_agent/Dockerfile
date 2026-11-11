# AISBench harbor_agent 运行环境
#
# 基于 release agent-runtime 镜像，额外校验 harbor venv 可用；
# benchmark 与 autoeval-adaptor 均以编辑模式安装。
# 容器启动方式由适配器按 params.env_isolation 选择 socket（默认）或 dind。
ARG BASE_IMAGE=ghcr.io/aisbench/agent-runtime:v3.1-20261013-master-ubuntu24.04-py312-x86_64
FROM ${BASE_IMAGE}

ARG BENCHMARK_VERSION=v3.1-20261013-master

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# benchmark 固定到指定版本，并保持编辑模式安装
RUN cd /benchmark \
    && if ! git rev-parse -q --verify "refs/tags/${BENCHMARK_VERSION}" >/dev/null; then \
        git fetch --tags origin \
            "refs/tags/${BENCHMARK_VERSION}:refs/tags/${BENCHMARK_VERSION}"; \
    fi \
    && git checkout "${BENCHMARK_VERSION}" \
    && python3.12 -m pip install --no-cache-dir --no-deps --no-build-isolation \
        --break-system-packages -e .

# 编辑模式安装 autoeval adaptor
COPY pyproject.toml /opt/autoeval-adaptor/pyproject.toml
COPY src /opt/autoeval-adaptor/src
RUN python3.12 -m pip install --no-cache-dir --no-deps --no-build-isolation \
        --break-system-packages -e /opt/autoeval-adaptor

# harbor venv 与 wrapper 是 agent-runtime 镜像的关键组成，构建时确保存在
RUN test -x /opt/venvs/harbor/bin/ais_bench \
    && /opt/venvs/harbor/bin/python -c "import harbor; print('harbor', harbor.__version__)"

WORKDIR /benchmark

CMD ["sleep", "infinity"]
