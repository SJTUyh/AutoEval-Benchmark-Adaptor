#!/usr/bin/env bash
# 启动 ais_bench/llm_accuracy 执行容器（镜像缺失时自动构建）。
# 用法: ./start.sh <config.json>
set -euo pipefail
CONFIG="${1:?用法: start.sh <config.json>}"
shift
exec autoeval-adaptor --config "$CONFIG" --start-env "$@"
