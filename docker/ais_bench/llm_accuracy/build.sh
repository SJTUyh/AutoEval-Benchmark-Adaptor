#!/usr/bin/env bash
# 构建 ais_bench/llm_accuracy 执行镜像。
# 用法: ./build.sh <config.json> [--force-rebuild]
set -euo pipefail
CONFIG="${1:?用法: build.sh <config.json> [--force-rebuild]}"
shift
exec autoeval-adaptor --config "$CONFIG" --build-env "$@"
