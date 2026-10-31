#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [[ ! -f config.json ]]; then
  echo "找不到配置文件: $PWD/config.json" >&2
  exit 1
fi

echo "正在启动 webai2api…"
echo "API 地址: http://127.0.0.1:8787/v1"
echo "健康检查: http://127.0.0.1:8787/health"
echo "按 Ctrl+C 停止服务"
echo
exec python3 webai2api.py run --config "$PWD/config.json"
