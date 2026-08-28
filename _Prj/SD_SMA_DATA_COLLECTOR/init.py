"""
系统初始化脚本
用于初始化系统环境和依赖
"""

import argparse
import os
import subprocess
import sys


def install_dependencies(requirements_file: str) -> bool:
    """安装指定 requirements 文件中的依赖"""
    if not os.path.exists(requirements_file):
        print(f"依赖文件不存在，跳过: {requirements_file}")
        return False
    print(f"正在安装依赖: {requirements_file}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", requirements_file])
        print("依赖安装完成!")
    except subprocess.CalledProcessError as e:
        print(f"依赖安装失败: {e}")
        return False
    return True


def create_directories():
    """创建必要的目录"""
    dirs = ["logs", "data", "output"]
    for dir_name in dirs:
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)
            print(f"创建目录: {dir_name}")


def main():
    """主初始化函数"""
    parser = argparse.ArgumentParser(description="SMA 数据采集系统初始化")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="同时安装开发/测试依赖（requirements-dev.txt）",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    runtime_requirements = os.path.join(script_dir, "requirements.txt")
    dev_requirements = os.path.join(script_dir, "requirements-dev.txt")

    print("=== BR数据采集系统初始化 ===")
    
    # 安装运行依赖
    if not install_dependencies(runtime_requirements):
        return False

    # 可选安装开发依赖
    if args.dev and not install_dependencies(dev_requirements):
        return False
    
    # 创建目录
    os.chdir(script_dir)
    create_directories()
    
    print("系统初始化完成!")
    print("\n使用说明:")
    print("1. 修改 config/sample_config.json 中的配置")
    print("2. 运行采集模式: python main.py")
    if not args.dev:
        print("3. 如需开发/测试依赖: python init.py --dev")
    
    return True


if __name__ == "__main__":
    main()
