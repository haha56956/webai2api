#!/usr/bin/env bash
set -euo pipefail

# 设置脚本目录
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR"

# 检查配置文件
if [[ ! -f config.json ]]; then
  echo "错误: 找不到配置文件: $SCRIPT_DIR/config.json" >&2
  echo "请先运行: python3 webai2api.py init --config config.json" >&2
  echo "然后编辑配置文件填入必要的 API tokens" >&2
  exit 1
fi

# 检查 Python 版本
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
REQUIRED_VERSION="3.10"

if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)"; then
  echo "错误: 需要 Python $REQUIRED_VERSION 或更高版本，当前版本: $PYTHON_VERSION" >&2
  exit 1
fi

# 检查必要的模块
python3 -c "import webai2api" 2>/dev/null || {
  echo "错误: 无法导入 webai2api 模块，请确保在正确的目录中运行" >&2
  exit 1
}

# 启动信息
echo "========================================"
echo "      webai2api 启动程序"
echo "========================================"
echo "配置文件: $SCRIPT_DIR/config.json"
echo "Python 版本: $PYTHON_VERSION"
echo "API 地址: http://127.0.0.1:8787/v1"
echo "健康检查: http://127.0.0.1:8787/health"
echo "Web UI: http://127.0.0.1:8787/ui"
echo "========================================"
echo "按 Ctrl+C 停止服务"
echo "========================================"
echo

# 检查配置有效性
echo "正在验证配置..."
if python3 -m webai2api check --config "$SCRIPT_DIR/config.json" > /dev/null 2>&1; then
  echo "✅ 配置验证通过"
else
  echo "⚠️  配置验证失败，但仍将尝试启动"
fi

# 启动服务
echo "正在启动 webai2api 服务..."
exec python3 webai2api.py run --config "$SCRIPT_DIR/config.json"
