FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    KW_TEMP_LOCAL_ROOT=/var/lib/knowwhere/temp \
    KW_FASTER_WHISPER_MODEL_DIR=/var/lib/knowwhere/models

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

# 默认使用腾讯云 Debian 镜像，构建时可通过同名参数切换其他镜像源。
ARG DEBIAN_MIRROR=https://mirrors.cloud.tencent.com/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirrors.cloud.tencent.com/debian-security

# 默认使用腾讯云 PyPI 镜像，并提高慢速网络下的下载容错能力。
ARG UV_DEFAULT_INDEX=https://mirrors.cloud.tencent.com/pypi/simple
ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_HTTP_RETRIES=5 \
    UV_HTTP_TIMEOUT=120

# 构建期 ASR 选择由 Compose 从 .env 传入，并决定是否安装本地推理 extra。
ARG KW_ASR_PROVIDER=faster_whisper

# FFmpeg 是视频音频标准化的运行时依赖；系统用户在复制项目文件前固定创建。
RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources && \
    apt-get \
        -o Acquire::Retries=5 \
        -o Acquire::http::Timeout=60 \
        -o Acquire::https::Timeout=60 \
        update && \
    apt-get \
        -o Acquire::Retries=5 \
        -o Acquire::http::Timeout=60 \
        -o Acquire::https::Timeout=60 \
        install --no-install-recommends --yes ffmpeg && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system knowwhere && \
    useradd --system --gid knowwhere --home-dir /app knowwhere && \
    mkdir --parents /var/lib/knowwhere/temp /var/lib/knowwhere/models && \
    chown --recursive knowwhere:knowwhere /var/lib/knowwhere

COPY pyproject.toml uv.lock ./

# 先安装锁文件中的生产依赖，使源码变更不会让第三方依赖层失效。
RUN --mount=type=cache,target=/root/.cache/uv \
    case "${KW_ASR_PROVIDER}" in \
        faster_whisper) uv sync --frozen --no-dev --no-install-project --extra local-asr ;; \
        tencent) uv sync --frozen --no-dev --no-install-project ;; \
        *) echo "不支持的 KW_ASR_PROVIDER: ${KW_ASR_PROVIDER}" >&2; exit 1 ;; \
    esac

COPY README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY third_party_licenses ./third_party_licenses
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

# 安装当前项目并复用 uv 下载缓存；生产依赖已在上一层完成安装。
RUN --mount=type=cache,target=/root/.cache/uv \
    case "${KW_ASR_PROVIDER}" in \
        faster_whisper) uv sync --frozen --no-dev --extra local-asr ;; \
        tencent) uv sync --frozen --no-dev ;; \
        *) echo "不支持的 KW_ASR_PROVIDER: ${KW_ASR_PROVIDER}" >&2; exit 1 ;; \
    esac
USER knowwhere

ENTRYPOINT ["/app/.venv/bin/knowwhere"]
CMD ["health"]
