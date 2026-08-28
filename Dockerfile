FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

# 安装依赖并创建非 root 运行用户，降低镜像层数和第三方组件漏洞影响面。
RUN uv sync --frozen --no-dev && \
    groupadd --system knowwhere && \
    useradd --system --gid knowwhere --home-dir /app knowwhere
USER knowwhere

ENTRYPOINT ["/app/.venv/bin/knowwhere"]
CMD ["health"]
