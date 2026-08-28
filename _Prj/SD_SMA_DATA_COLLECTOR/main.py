"""
数据采集系统主应用程序（CLI 入口）
运行时核心已抽离至 runtime.collector_runtime
"""

import argparse
import asyncio
import os
import sys

# 处理相对导入问题
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from runtime.collector_runtime import run_collection_mode
except ImportError as e:
    print(f"导入模块时发生错误: {e}")
    print("请确保所有依赖包已正确安装")
    sys.exit(1)


def main():
    """主函数（仅负责参数解析与调度）"""
    parser = argparse.ArgumentParser(description="BR数据采集系统")
    parser.add_argument("--config", "-c", default="config/sample_config.json", help="配置文件路径")
    args = parser.parse_args()

    try:
        asyncio.run(run_collection_mode(args.config))
    except KeyboardInterrupt:
        print("\n收到中断信号，已退出。")


if __name__ == "__main__":
    main()
