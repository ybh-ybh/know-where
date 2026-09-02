FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

# FFmpeg 是视频音频标准化的运行时依赖；系统用户在复制项目文件前固定创建。
RUN apt-get update && \
    apt-get install --no-install-recommends --yes ffmpeg && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system knowwhere && \
    useradd --system --gid knowwhere --home-dir /app knowwhere

COPY pyproject.toml uv.lock README.md ./
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY third_party_licenses ./third_party_licenses
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

# 使用锁文件安装生产依赖并编译字节码。
RUN uv sync --frozen --no-dev
USER knowwhere

ENTRYPOINT ["/app/.venv/bin/knowwhere"]
CMD ["health"]
