from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .config import (
    ConfigError,
    default_config_path,
    init_config,
    load_config,
    validate_config,
)
from .pow import PowError, solve_pow

POW_VECTOR = {
    "challenge": "b4904764f21e49261b7e8aa1cca16f32451d54ebcd65d13037083f0c4b4d31b0",
    "salt": "865fc16c1f6c7b648547",
    "expire_at": "1784018472563",
    "difficulty": 144000,
    "answer": 105512,
}


def _config_arg(args: argparse.Namespace, for_init: bool = False) -> Path:
    if getattr(args, "config", None):
        return Path(args.config).expanduser().resolve()
    if for_init:
        return (Path.cwd() / "config.json").resolve()
    return default_config_path().expanduser().resolve()


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def cmd_init(args: argparse.Namespace) -> int:
    path = _config_arg(args, for_init=True)
    try:
        created = init_config(path, force=args.force)
    except ConfigError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    print(f"已创建配置文件: {created}")
    print("请编辑该文件，至少填写 providers.deepseek.token 或 providers.qwen.token/cookies。")
    print(f"启动: python3 {Path(__file__).resolve().parent.parent / 'webai2api.py'} run --config {created}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    path = _config_arg(args)
    try:
        cfg = load_config(path)
        warnings = validate_config(cfg)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1
    if args.pow:
        try:
            answer = solve_pow(
                POW_VECTOR["salt"],
                POW_VECTOR["expire_at"],
                POW_VECTOR["challenge"],
                POW_VECTOR["difficulty"] + 1,
            )
        except PowError as exc:
            print(f"PoW 自检失败: {exc}", file=sys.stderr)
            return 1
        if answer != POW_VECTOR["answer"]:
            print(f"PoW 自检失败: 期望 {POW_VECTOR['answer']}，得到 {answer}", file=sys.stderr)
            return 1
        print("PoW 自检通过")
    print(f"配置文件: {path}")
    print(f"监听地址: {cfg['listen']['host']}:{cfg['listen']['port']}")
    print(f"全局 RPM: {cfg['rpm'] or '不限'}（窗口 {cfg['rpm_window']} 秒）")
    queue_cfg = cfg.get("queue") or {}
    if queue_cfg.get("enabled"):
        print(
            f"超限排队: 队列上限 {queue_cfg.get('max_size')}，"
            f"最长等待 {queue_cfg.get('max_wait')} 秒"
        )
    else:
        print("超限排队: 已禁用（超限直接返回 429）")
    fallback_cfg = cfg.get("fallback") or {}
    if fallback_cfg.get("enabled"):
        print(
            f"自动路由: 最多 {fallback_cfg.get('max_attempts')} 个 provider，"
            f"顺序 {fallback_cfg.get('order') or '默认'}"
        )
    for name, provider in cfg["providers"].items():
        if not provider.get("enabled"):
            continue
        models = ", ".join(provider.get("models", {}).keys()) or "无"
        if name == "duckduckgo":
            vqd = "已配置" if provider.get("vqd_hash") else "未配置"
            fe_version = provider.get("fe_version") or "自动获取"
            fe_signals = "已配置" if provider.get("fe_signals") else "未配置"
            print(
                f"- {name}: vqd_hash={vqd} fe_version={fe_version} "
                f"fe_signals={fe_signals} models={models}"
            )
        else:
            token = "已配置" if provider.get("token") else "未配置"
            cookies = "已配置" if provider.get("cookies") else "未配置"
            print(f"- {name}: token={token} cookies={cookies} models={models}")
    for warning in warnings:
        print(f"警告: {warning}", file=sys.stderr)
    print("配置检查通过")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    path = _config_arg(args)
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1
    level = args.log_level or os.environ.get("WEBAI2API_LOG_LEVEL") or cfg.get("log_level", "INFO")
    _setup_logging(level)
    from .server import serve

    try:
        serve(str(path), host=args.host, port=args.port)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        return 1
    return 0


def _service_paths(name: str) -> tuple[Path, Path]:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    return unit_dir, unit_dir / f"{name}.service"


def _systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    command = ["systemctl", "--user", *args]
    try:
        return subprocess.run(command, capture_output=True, text=True, check=check)
    except FileNotFoundError:
        print("错误: 找不到 systemctl，无法管理 systemd 服务", file=sys.stderr)
        raise


def cmd_install_service(args: argparse.Namespace) -> int:
    if os.geteuid() == 0:
        print("提示: 当前以 root 运行，仍将安装到当前用户的 systemd user 目录。")
    name = args.name
    if not all(ch.isalnum() or ch in "-_." for ch in name):
        print("错误: 服务名只能包含字母、数字、-、_、.", file=sys.stderr)
        return 1
    config_path = _config_arg(args)
    if not config_path.exists():
        print(f"错误: 配置文件不存在: {config_path}", file=sys.stderr)
        print(f"请先运行: python3 {sys.argv[0]} init --config {config_path}", file=sys.stderr)
        return 1
    try:
        load_config(config_path)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1

    project_root = Path(__file__).resolve().parent.parent
    entry = project_root / "webai2api.py"
    if entry.exists():
        exec_args = [sys.executable, str(entry), "run", "--config", str(config_path)]
    else:
        exec_args = [sys.executable, "-m", "webai2api", "run", "--config", str(config_path)]
    unit_dir, unit_path = _service_paths(name)
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = f"""[Unit]
Description=webai2api OpenAI-compatible web AI gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={project_root}
Environment=PYTHONUNBUFFERED=1
ExecStart={shlex.join(exec_args)}
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
"""
    unit_path.write_text(unit, encoding="utf-8")
    print(f"已写入服务文件: {unit_path}")
    try:
        _systemctl("daemon-reload")
        result = _systemctl("enable", "--now", name)
    except FileNotFoundError:
        return 1
    started = result.returncode == 0
    if not started:
        print(
            f"警告: systemctl 启用服务失败: {result.stderr.strip() or result.stdout.strip()}",
            file=sys.stderr,
        )
        print("服务文件已写入，可在有 systemd 用户会话的环境中手动启用。", file=sys.stderr)
    if not args.no_linger and shutil.which("loginctl"):
        result = subprocess.run(
            ["loginctl", "enable-linger", os.environ.get("USER", "")],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                "警告: loginctl enable-linger 失败，注销登录后服务可能停止。"
                f" 错误: {result.stderr.strip()}",
                file=sys.stderr,
            )
        else:
            print("已启用 linger，开机后可自动启动用户服务")
    if started:
        print(f"已启用并启动服务: {name}")
    print(f"查看状态: systemctl --user status {name}")
    print(f"查看日志: journalctl --user -u {name} -f")
    return 0


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    name = args.name
    _, unit_path = _service_paths(name)
    if not unit_path.exists():
        print(f"服务文件不存在: {unit_path}")
        return 0
    try:
        _systemctl("disable", "--now", name)
    except FileNotFoundError:
        return 1
    try:
        unit_path.unlink()
    except OSError as exc:
        print(f"删除服务文件失败: {exc}", file=sys.stderr)
        return 1
    try:
        _systemctl("daemon-reload")
    except FileNotFoundError:
        return 1
    print(f"已卸载服务: {name}（配置文件已保留）")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    name = args.name
    _, unit_path = _service_paths(name)
    print(f"服务文件: {unit_path} ({'存在' if unit_path.exists() else '不存在'})")
    if unit_path.exists() and shutil.which("systemctl"):
        for verb, label in (("is-active", "运行状态"), ("is-enabled", "开机启动")):
            result = _systemctl(verb, name)
            print(f"{label}: {result.stdout.strip() or result.stderr.strip()}")
    path = _config_arg(args)
    if path.exists():
        try:
            cfg = load_config(path)
        except ConfigError as exc:
            print(f"配置错误: {exc}", file=sys.stderr)
            return 1
        print(f"配置文件: {path}")
        print(f"监听地址: {cfg['listen']['host']}:{cfg['listen']['port']}")
        print(f"已启用 provider: {', '.join(n for n, p in cfg['providers'].items() if p.get('enabled'))}")
    else:
        print(f"配置文件不存在: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webai2api",
        description="把 DeepSeek / Qwen / DuckDuckGo 网页聊天封装为 OpenAI 兼容 API",
    )
    parser.add_argument("--version", action="version", version=f"webai2api {__version__}")
    sub = parser.add_subparsers(dest="command")

    init_parser = sub.add_parser("init", help="生成示例配置文件")
    init_parser.add_argument("--config", help="配置文件路径")
    init_parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    init_parser.set_defaults(func=cmd_init)

    run_parser = sub.add_parser("run", help="启动 API 服务")
    run_parser.add_argument("--config", help="配置文件路径")
    run_parser.add_argument("--host", help="覆盖监听地址")
    run_parser.add_argument("--port", type=int, help="覆盖监听端口")
    run_parser.add_argument("--log-level", help="日志级别: DEBUG/INFO/WARNING/ERROR")
    run_parser.set_defaults(func=cmd_run)

    check_parser = sub.add_parser("check", help="检查配置和 PoW 实现")
    check_parser.add_argument("--config", help="配置文件路径")
    check_parser.add_argument("--pow", action="store_true", help="运行 DeepSeek PoW 自检")
    check_parser.set_defaults(func=cmd_check)

    install_parser = sub.add_parser("install-service", help="安装并启动 systemd 用户服务（开机自启）")
    install_parser.add_argument("--config", help="配置文件路径")
    install_parser.add_argument("--name", default="webai2api", help="systemd 服务名")
    install_parser.add_argument("--no-linger", action="store_true", help="不启用 loginctl linger")
    install_parser.set_defaults(func=cmd_install_service)

    uninstall_parser = sub.add_parser("uninstall-service", help="停止并卸载 systemd 用户服务")
    uninstall_parser.add_argument("--name", default="webai2api", help="systemd 服务名")
    uninstall_parser.set_defaults(func=cmd_uninstall_service)

    status_parser = sub.add_parser("status", help="查看服务和配置状态")
    status_parser.add_argument("--config", help="配置文件路径")
    status_parser.add_argument("--name", default="webai2api", help="systemd 服务名")
    status_parser.set_defaults(func=cmd_status)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    raw = list(argv) if argv is not None else sys.argv[1:]
    commands = {"init", "run", "check", "install-service", "uninstall-service", "status"}
    if raw and raw[0] not in commands and raw[0] not in ("-h", "--help", "--version"):
        raw = ["run", *raw]
    args = parser.parse_args(raw)
    if not getattr(args, "command", None):
        run_args = parser.parse_args(["run"])
        return run_args.func(run_args)
    return args.func(args)
