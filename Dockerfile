FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh ./
RUN chmod +x ./entrypoint.sh && mkdir -p /app/data

EXPOSE 8000

# 初始化（建表 + 可选示例资料）后一键启动
ENTRYPOINT ["./entrypoint.sh"]
CMD ["serve"]
