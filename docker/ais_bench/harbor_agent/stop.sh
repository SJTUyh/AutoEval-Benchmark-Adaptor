#!/usr/bin/env bash
# 停止并删除 ais_bench/harbor_agent 执行容器。
# 用法: ./stop.sh <config.json>
set -euo pipefail
CONFIG="${1:?用法: stop.sh <config.json>}"
shift
exec autoeval-adaptor --config "$CONFIG" --stop-env "$@"
