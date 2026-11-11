# AISBench llm_accuracy 运行环境
#
# 基于 release agent-runtime 镜像（ubuntu24.04 + py3.12 + AISBench + harbor venv），
# 在其上以编辑模式安装指定版本的 benchmark 与 autoeval-adaptor。
# 该子命令不需要 harbor，也不需要容器内 docker，仅用于跑服务化模型精度测评。
ARG BASE_IMAGE=ghcr.io/aisbench/agent-runtime:v3.1-20261013-master-ubuntu24.04-py312-x86_64
FROM ${BASE_IMAGE}

ARG BENCHMARK_VERSION=v3.1-20261013-master
# 可选：从个人仓/分支安装最新 benchmark 源码
ARG BENCHMARK_REPO=""
ARG BENCHMARK_REF=""
ARG BENCHMARK_REV=0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# benchmark 固定到指定版本或个人仓分支，并保持编辑模式安装
RUN cd /benchmark \
    && echo "benchmark source: ${BENCHMARK_REPO:-AISBench/benchmark}@${BENCHMARK_REF:-${BENCHMARK_VERSION}} rev=${BENCHMARK_REV}" \
    && if [ -n "${BENCHMARK_REPO}" ]; then \
        git remote remove autoeval-fork >/dev/null 2>&1 || true; \
        git remote add autoeval-fork "${BENCHMARK_REPO}"; \
        git fetch --depth 1 autoeval-fork "${BENCHMARK_REF}"; \
        git checkout -B autoeval-fork FETCH_HEAD; \
    else \
        if ! git rev-parse -q --verify "refs/tags/${BENCHMARK_VERSION}" >/dev/null; then \
            git fetch --tags origin \
                "refs/tags/${BENCHMARK_VERSION}:refs/tags/${BENCHMARK_VERSION}"; \
        fi; \
        git checkout "${BENCHMARK_VERSION}"; \
    fi \
    && python3.12 -m pip install --no-cache-dir --no-deps --no-build-isolation \
        --break-system-packages -e .

# 编辑模式安装 autoeval adaptor
COPY pyproject.toml /opt/autoeval-adaptor/pyproject.toml
COPY src /opt/autoeval-adaptor/src
RUN python3.12 -m pip install --no-cache-dir --no-deps --no-build-isolation \
        --break-system-packages -e /opt/autoeval-adaptor

WORKDIR /benchmark

CMD ["sleep", "infinity"]
