"""配置兼容性测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from knowwhere.config import AppSettings, TencentCloudSettings

# 配置加载器识别的变量名，测试时先清除宿主机覆盖。
CONFIG_KEYS = (
    "KW_DATABASE_URL",
    "KW_FEISHU_APP_ID",
    "KW_FEISHU_APP_SECRET",
    "KW_LLM_API_KEY",
    "KW_LLM_BASE_URL",
    "KW_LLM_MODEL",
    "KW_LLM_TIMEOUT_SECONDS",
    "KW_LLM_THINKING_MODE",
    "KW_TENCENTCLOUD_APP_ID",
    "KW_TENCENTCLOUD_SECRET_ID",
    "KW_TENCENTCLOUD_SECRET_KEY",
    "KW_COS_REGION",
    "KW_COS_BUCKET",
    "KW_COS_PREFIX",
    "KW_COS_PRESIGNED_URL_TTL_SECONDS",
    "KW_TENCENT_ASR_ENGINE_MODEL_TYPE",
    "KW_TENCENT_ASR_POLL_INTERVAL_SECONDS",
    "KW_TENCENT_ASR_MAX_WAIT_SECONDS",
    "KW_VISION_API_KEY",
    "KW_VISION_BASE_URL",
    "KW_VISION_MODEL",
    "KW_VISION_TIMEOUT_SECONDS",
    "KW_VISION_THINKING_MODE",
    "KW_DOUYIN_REQUEST_TIMEOUT_SECONDS",
    "KW_DOUYIN_MAX_IMAGE_BYTES",
    "KW_DOUYIN_MAX_VIDEO_BYTES",
    "KW_DOUYIN_MAX_IMAGES",
    "KW_FFMPEG_PATH",
)


# 外部配置可能把纯数字 AppId 作为整数传入，运行时应安全规范化。
def test_numeric_tencent_app_id_is_normalized() -> None:
    """验证腾讯云数字 AppId。"""

    # 最小腾讯云配置。
    settings = TencentCloudSettings.model_validate(
        {
            "app_id": 1234567890,
            "secret_id": "test-id",
            "secret_key": "test-key",
            "cos": {
                "region": "ap-shanghai",
                "bucket": "test-1234567890",
                "prefix": "knowwhere-temp/",
            },
        }
    )

    assert settings.app_id == "1234567890"


# .env 使用通用 LLM 变量且允许文章 MVP 不配置腾讯云。
def test_env_file_loads_generic_llm_without_tencentcloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证扁平环境变量配置。"""

    for key in CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 临时环境文件。
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "KW_FEISHU_APP_ID=cli_test",
                "KW_FEISHU_APP_SECRET=feishu-secret",
                "KW_LLM_API_KEY=llm-secret",
                "KW_LLM_BASE_URL=https://llm.example/v1",
                "KW_LLM_MODEL=test-model",
                "KW_LLM_TIMEOUT_SECONDS=120",
                "KW_DATABASE_URL=postgresql+psycopg://localhost/knowwhere",
            )
        ),
        encoding="utf-8",
    )

    # 已校验配置。
    settings = AppSettings.load(env_file)

    assert settings.llm.model == "test-model"
    assert settings.llm.timeout_seconds == 120
    assert settings.llm.thinking_mode == "disabled"
    assert settings.tencentcloud is None


# 进程环境变量应覆盖 .env，便于容器安全注入。
def test_process_environment_overrides_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证配置优先级。"""

    for key in CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 基础环境文件。
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "KW_FEISHU_APP_ID=cli_test",
                "KW_FEISHU_APP_SECRET=feishu-secret",
                "KW_LLM_API_KEY=llm-secret",
                "KW_LLM_BASE_URL=https://llm.example/v1",
                "KW_LLM_MODEL=file-model",
                "KW_DATABASE_URL=postgresql+psycopg://localhost/knowwhere",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KW_LLM_MODEL", "environment-model")

    # 已覆盖配置。
    settings = AppSettings.load(env_file)

    assert settings.llm.model == "environment-model"


# 视觉模型使用独立变量，并规范控制台显示名称和根地址。
def test_env_file_loads_independent_vision_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证视觉模型配置隔离。"""

    for key in CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 包含文本与视觉两套模型的环境文件。
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "KW_FEISHU_APP_ID=cli_test",
                "KW_FEISHU_APP_SECRET=feishu-secret",
                "KW_LLM_API_KEY=llm-secret",
                "KW_LLM_BASE_URL=https://llm.example/v1",
                "KW_LLM_MODEL=text-model",
                "KW_VISION_API_KEY=vision-secret",
                "KW_VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4/",
                "KW_VISION_MODEL=GLM-4.6V",
                "KW_DATABASE_URL=postgresql+psycopg://localhost/knowwhere",
            )
        ),
        encoding="utf-8",
    )

    # 已校验配置。
    settings = AppSettings.load(env_file)

    assert settings.vision is not None
    assert settings.vision.model == "glm-4.6v"
    assert settings.vision.base_url == "https://open.bigmodel.cn/api/paas/v4"


# 无效 LLM 地址必须在启动配置阶段失败，而不是等到首次付费请求。
def test_invalid_llm_base_url_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LLM 根地址格式。"""

    for key in CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 包含无效地址的环境文件。
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "KW_FEISHU_APP_ID=cli_test",
                "KW_FEISHU_APP_SECRET=feishu-secret",
                "KW_LLM_API_KEY=llm-secret",
                "KW_LLM_BASE_URL=missing-scheme.example/v1",
                "KW_LLM_MODEL=test-model",
                "KW_DATABASE_URL=postgresql+psycopg://localhost/knowwhere",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="KW_LLM_BASE_URL"):
        AppSettings.load(env_file)


# 数据库连接包含凭据，缺少环境配置时必须失败而不能回退到代码默认值。
def test_database_url_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证数据库地址没有硬编码回退值。"""

    for key in CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 不包含数据库地址的最小环境文件。
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "KW_FEISHU_APP_ID=cli_test",
                "KW_FEISHU_APP_SECRET=feishu-secret",
                "KW_LLM_API_KEY=llm-secret",
                "KW_LLM_BASE_URL=https://llm.example/v1",
                "KW_LLM_MODEL=test-model",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="KW_DATABASE_URL"):
        AppSettings.load(env_file)
