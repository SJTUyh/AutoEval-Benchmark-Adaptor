#!/usr/bin/env bash
# 启动 ais_bench/harbor_agent 执行容器（镜像缺失时自动构建）。
# 默认 socket 模式；强隔离用: ./start.sh <config.json> --env-isolation dind
set -euo pipefail
CONFIG="${1:?用法: start.sh <config.json> [--env-isolation socket|dind]}"
shift
exec autoeval-adaptor --config "$CONFIG" --start-env "$@"
