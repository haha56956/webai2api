#!/usr/bin/env python3
"""
基本功能测试脚本
"""
import json
import subprocess
import sys
import time
from pathlib import Path

def run_command(cmd, check=True):
    """运行命令并返回结果"""
    print(f"执行: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        if result.stdout:
            print(f"输出: {result.stdout}")
        if result.stderr and check:
            print(f"错误: {result.stderr}")
        return result
    except subprocess.CalledProcessError as e:
        print(f"命令执行失败: {e}")
        print(f"错误输出: {e.stderr}")
        return e

def test_import():
    """测试模块导入"""
    print("=== 测试1: 模块导入 ===")
    result = run_command([sys.executable, "-c", "import webai2api; print('导入成功')"])
    return result.returncode == 0

def test_cli_commands():
    """测试CLI命令"""
    print("=== 测试2: CLI命令 ===")
    commands = [
        [sys.executable, "-m", "webai2api", "--help"],
        [sys.executable, "-m", "webai2api", "--version"],
    ]
    
    all_passed = True
    for cmd in commands:
        result = run_command(cmd, check=False)
        if result.returncode != 0:
            all_passed = False
            break
    
    return all_passed

def test_config_validation():
    """测试配置验证"""
    print("=== 测试3: 配置验证 ===")
    if not Path("config.json").exists():
        print("配置文件不存在，跳过此测试")
        return True
    
    result = run_command([sys.executable, "-m", "webai2api", "check", "--config", "config.json"])
    return result.returncode == 0

def test_server_startup():
    """测试服务器启动（短暂）"""
    print("=== 测试4: 服务器启动测试 ===")
    if not Path("config.json").exists():
        print("配置文件不存在，跳过此测试")
        return True
    
    # 启动服务器
    proc = subprocess.Popen([
        sys.executable, "-m", "webai2api", "run", 
        "--config", "config.json",
        "--host", "127.0.0.1",
        "--port", "8788"  # 使用不同端口避免冲突
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    try:
        # 等待服务器启动
        time.sleep(3)
        
        # 测试健康检查
        result = run_command([
            "curl", "-s", "http://127.0.0.1:8788/health"
        ], check=False)
        
        if result.returncode == 0:
            print("✅ 服务器启动成功，健康检查通过")
            success = True
        else:
            print("❌ 健康检查失败")
            success = False
        
    finally:
        # 终止服务器
        proc.terminate()
        proc.wait()
    
    return success

def main():
    """主测试函数"""
    print("webai2api 基本功能测试")
    print("=" * 50)
    
    tests = [
        ("模块导入", test_import),
        ("CLI命令", test_cli_commands),
        ("配置验证", test_config_validation),
        ("服务器启动", test_server_startup),
    ]
    
    results = []
    for name, test_func in tests:
        print(f"\n测试 {name}...")
        try:
            result = test_func()
            status = "✅ 通过" if result else "❌ 失败"
            print(f"{name}: {status}")
            results.append((name, result))
        except Exception as e:
            print(f"{name}: ❌ 异常 - {e}")
            results.append((name, False))
    
    print("\n" + "=" * 50)
    print("测试总结:")
    passed = sum(1 for _, result in results if result)
    total = len(results)
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("🎉 所有测试通过！")
        return 0
    else:
        print("⚠️  部分测试失败")
        return 1

if __name__ == "__main__":
    sys.exit(main())