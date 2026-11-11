#!/usr/bin/env bash
# 构建 ais_bench/harbor_agent 执行镜像。
#
# 用法:
#   ./build.sh <config.json> [--force-rebuild]
#   ./build.sh <config.json> --benchmark-source <个人仓分支 URL>
#   ./build.sh <config.json> --benchmark-source <仓库 URL> --benchmark-ref <分支/tag>
#
# 示例:
#   ./build.sh config.json \
#     --benchmark-source https://github.com/SJTUyh/benchmark/tree/autoeval
set -euo pipefail
CONFIG="${1:?用法: build.sh <config.json> [--benchmark-source URL] [--benchmark-ref REF] [--force-rebuild]}"
shift
exec autoeval-adaptor --config "$CONFIG" --build-env "$@"
