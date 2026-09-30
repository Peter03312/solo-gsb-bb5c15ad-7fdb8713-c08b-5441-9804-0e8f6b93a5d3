"""运行期配置，全部来自环境变量，便于容器化部署。"""

from __future__ import annotations

import os


def _sqlite_path(url: str) -> str:
    if not url.startswith("sqlite:///"):
        # 仅支持 SQLite；其他驱动前缀直接给出明确报错
        raise RuntimeError(f"不支持的 DATABASE_URL: {url!r}，仅支持 sqlite:/// 相对/绝对路径")
    return url[len("sqlite:///") :]


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/stowage.db")
DB_PATH = _sqlite_path(DATABASE_URL)

# 求解器单次回溯允许访问的最大节点数，防止极端算例长时间占用
SOLVER_NODE_BUDGET = int(os.getenv("SOLVER_NODE_BUDGET", "2000000"))

# 启动时若库为空是否自动写入示例资料（演示用，默认关闭；compose 中打开）
SEED_SAMPLE = os.getenv("SEED_SAMPLE", "0") == "1"

# 服务监听参数（entrypoint / 本地启动读取）
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
