#!/bin/sh
# 容器入口：先初始化数据库（建表；SEED_SAMPLE=1 且库为空时写入示例资料），再启动服务。
set -e

echo "[entrypoint] 初始化数据库 ..."
python -c "from app import db; from app import config; db.init_db(seed_sample=config.SEED_SAMPLE)"

case "${1:-serve}" in
  serve)
    echo "[entrypoint] 启动 uvicorn on ${HOST:-0.0.0.0}:${PORT:-8000} ..."
    exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
    ;;
  init)
    echo "[entrypoint] 仅执行初始化后退出。"
    exit 0
    ;;
  *)
    exec "$@"
    ;;
esac
