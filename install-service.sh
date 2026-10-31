#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR"

# 检查root权限
if [[ $EUID -eq 0 ]]; then
    echo "警告: 不建议以root用户运行此脚本" >&2
fi

# 检查systemd
if ! systemctl --user status > /dev/null 2>&1 && ! systemctl status > /dev/null 2>&1; then
    echo "错误: system 不可用，无法安装服务" >&2
    exit 1
fi

# 检查Python
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)"; then
    echo "错误: 需要 Python 3.10 或更高版本，当前版本: $PYTHON_VERSION" >&2
    exit 1
fi

# 检查配置文件
if [[ ! -f config.json ]]; then
    echo "正在创建初始配置文件..."
    python3 webai2api.py init --config config.json --force
    echo "请编辑 config.json 文件并填入必要的 API tokens"
    echo "编辑完成后运行此脚本继续安装"
    exit 0
fi

# 验证配置
echo "正在验证配置..."
if ! python3 -m webai2api check --config "$SCRIPT_DIR/config.json" > /dev/null 2>&1; then
    echo "配置验证失败，请检查配置文件"
    exit 1
fi

# 服务名称
read -p "输入服务名称 (默认: webai2api): " SERVICE_NAME
SERVICE_NAME=${SERVICE_NAME:-webai2api}

# 安装服务
echo "正在安装 systemd 服务: $SERVICE_NAME"
python3 webai2api.py install-service --config "$SCRIPT_DIR/config.json" --name "$SERVICE_NAME"

echo "========================================"
echo "      服务安装完成"
echo "========================================"
echo "服务名称: $SERVICE_NAME"
echo "启动命令: systemctl --user start $SERVICE_NAME"
echo "停止命令: systemctl --user stop $SERVICE_NAME"
echo "状态查看: systemctl --user status $SERVICE_NAME"
echo "查看日志: journalctl --user -u $SERVICE_NAME -f"
echo "重启服务: systemctl --user restart $SERVICE_NAME"
echo "开机自启: systemctl --user enable $SERVICE_NAME"
echo "========================================"
echo "如需卸载服务: python3 webai2api.py uninstall-service --name $SERVICE_NAME"
echo "========================================"