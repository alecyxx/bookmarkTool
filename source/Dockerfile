# Bookmark Manager V1 生产镜像（BM-V1-801）
# 构建上下文：source/（.dockerignore 排除开发产物）
# 固定补丁版本基础镜像：python:3.14.3-slim-bookworm
#   如需 pin digest（推荐），先用 `docker buildx imagetools inspect python:3.14.3-slim-bookworm`
#   查询目标平台的 digest 后替换下一行（示例：
#   FROM python:3.14.3-slim-bookworm@sha256:<digest>）
FROM python:3.14.3-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

# 非 root 运行用户（固定 uid：宿主 bind 挂载备份目录需与此一致，见运维手册）
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home --home-dir /app app

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

# 应用代码只读：/app 挂为 ro（compose 中设置）
COPY --chown=app:app . /app

# 数据与临时目录独立可写（卷挂载 /data）
RUN mkdir -p /data && chown app:app /data && chmod 700 /data

USER app

EXPOSE 8000

# 生产入口：uvicorn 工厂 + 单 worker（SQLite 单写者模型）
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
