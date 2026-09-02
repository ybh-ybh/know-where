"""从受限 .env 与进程环境变量加载配置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, field_validator

# 默认本地环境文件；容器也可以只注入进程环境变量。
DEFAULT_ENV_FILE = Path(".env")


# 读取可选环境变量并把空字符串规范为未配置。
def _optional_value(values: dict[str, str], key: str) -> str | None:
    """返回非空环境变量。"""

    # 当前变量值。
    value = values.get(key)
    return value if value else None


# 合并 .env 与进程环境变量，后者始终具有更高优先级。
def _load_env_values(path: Path | None) -> dict[str, str]:
    """读取配置源并返回字符串映射。"""

    # 显式或默认环境文件路径。
    env_file = path or Path(os.getenv("KW_ENV_FILE", DEFAULT_ENV_FILE))
    # 文件中的非空键值；默认文件不存在时允许纯环境变量部署。
    file_values = (
        {
            key: value
            for key, value in dotenv_values(env_file, encoding="utf-8").items()
            if value is not None
        }
        if env_file.is_file()
        else {}
    )
    # 进程环境覆盖文件值，便于 Docker Secret 或编排平台注入。
    return {**file_values, **dict(os.environ)}


# 飞书应用配置。
class FeishuSettings(BaseModel):
    """飞书应用凭据。"""

    # 飞书应用 ID。
    app_id: str
    # 飞书应用密钥。
    app_secret: SecretStr


# 腾讯云 COS 普通配置。
class CosSettings(BaseModel):
    """COS Bucket 配置。"""

    # Bucket 地域。
    region: str
    # 完整 Bucket 名称。
    bucket: str
    # 临时对象前缀。
    prefix: str = "knowwhere-temp/"
    # 私有对象下载签名有效期。
    presigned_url_ttl_seconds: int = Field(default=3600, ge=300, le=86400)

    # 统一临时对象前缀，避免对象落到 Bucket 根目录。
    @field_validator("prefix")
    @classmethod
    def normalize_prefix(cls, value: str) -> str:
        """返回非空且以斜杠结尾的相对前缀。"""

        # 清除可能扩大对象范围的首尾分隔符。
        normalized_value = value.strip().strip("/")
        if not normalized_value or ".." in normalized_value.split("/"):
            raise ValueError("KW_COS_PREFIX 必须是非空安全相对前缀")
        return f"{normalized_value}/"


# 腾讯云录音文件识别配置。
class AsrSettings(BaseModel):
    """腾讯云 ASR 轮询参数。"""

    # 识别引擎，默认支持中英文混合普通话。
    engine_model_type: str = "16k_zh"
    # 状态轮询间隔秒数。
    poll_interval_seconds: float = Field(default=3.0, ge=0.5, le=30)
    # 单个识别任务最大等待秒数。
    max_wait_seconds: float = Field(default=1800.0, ge=30, le=21600)


# 腾讯云共享凭据配置。
class TencentCloudSettings(BaseModel):
    """腾讯云 ASR 与 COS 配置。"""

    # 腾讯云账号 AppId。
    app_id: str
    # 腾讯云 SecretId。
    secret_id: SecretStr
    # 腾讯云 SecretKey。
    secret_key: SecretStr
    # COS 配置。
    cos: CosSettings
    # 录音文件识别配置。
    asr: AsrSettings = Field(default_factory=AsrSettings)

    # 数字 AppId 也规范为领域外字符串。
    @field_validator("app_id", mode="before")
    @classmethod
    def normalize_app_id(cls, value: object) -> object:
        """兼容腾讯云控制台常见的纯数字 AppId。"""

        return str(value) if value is not None else value


# 单个 OpenAI 兼容供应商配置。
class LlmProviderSettings(BaseModel):
    """OpenAI 兼容 LLM 配置。"""

    # 模型 API Key。
    api_key: SecretStr
    # OpenAI 兼容根地址。
    base_url: str
    # 实际模型标识。
    model: str
    # 单次读取超时，长正文模型可通过配置调大。
    timeout_seconds: float = Field(default=180.0, gt=0, le=600)
    # 默认关闭思考模式，避免推理模型只返回思考内容而正文为空。
    thinking_mode: Literal["enabled", "disabled"] | None = "disabled"

    # 在启动阶段拒绝缺少协议或主机名的 LLM 地址。
    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """校验 OpenAI 兼容接口根地址。"""

        # 标准 URL 解析结果。
        parsed_url = urlsplit(value)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("KW_LLM_BASE_URL 必须是有效的 HTTP(S) 地址")
        return value


# 图文视觉模型配置与文本分析模型隔离，避免密钥和模型名互相覆盖。
class VisionModelSettings(BaseModel):
    """OpenAI 兼容视觉模型配置。"""

    # 视觉模型 API Key。
    api_key: SecretStr
    # OpenAI 兼容根地址。
    base_url: str
    # 视觉模型标识。
    model: str
    # 单次多图请求超时。
    timeout_seconds: float = Field(default=300.0, gt=0, le=900)
    # 智谱兼容思考模式。
    thinking_mode: Literal["enabled", "disabled"] | None = "disabled"

    # 拒绝 Markdown 链接和无协议地址，并移除末尾斜杠。
    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """校验视觉模型接口根地址。"""

        # 标准 URL 解析结果。
        parsed_url = urlsplit(value.strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("KW_VISION_BASE_URL 必须是有效的 HTTP(S) 地址")
        return value.strip().rstrip("/")

    # 官方模型 ID 使用小写，兼容用户从控制台复制的显示名称。
    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        """规范视觉模型 ID。"""

        return value.strip().lower()


# 抖音媒体下载和本地音频标准化配置。
class DouyinSettings(BaseModel):
    """抖音公开作品处理限制。"""

    # 页面和媒体请求超时。
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    # 单张图片最大字节数。
    max_image_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    # 单个视频最大字节数。
    max_video_bytes: int = Field(
        default=4 * 1024 * 1024 * 1024,
        ge=1024,
        le=20 * 1024 * 1024 * 1024,
    )
    # 单个作品最大图片数量。
    max_images: int = Field(default=30, ge=1, le=50)
    # FFmpeg 可执行文件名或绝对路径。
    ffmpeg_path: str = "ffmpeg"


# 应用完整配置快照。
class AppSettings(BaseModel):
    """知归运行配置。"""

    # 飞书配置。
    feishu: FeishuSettings
    # 腾讯云配置；文章 MVP 不启用视频能力时可以不配置。
    tencentcloud: TencentCloudSettings | None = None
    # 唯一启用的 OpenAI 兼容 LLM 配置。
    llm: LlmProviderSettings
    # 图文视觉模型；未配置时文章与视频仍可运行。
    vision: VisionModelSettings | None = None
    # 抖音媒体下载限制与工具路径。
    douyin: DouyinSettings = Field(default_factory=DouyinSettings)
    # PostgreSQL 连接地址；必须由环境提供，禁止在代码中设置凭据默认值。
    database_url: str

    # 读取 .env 并叠加进程环境变量。
    @classmethod
    def load(cls, path: Path | None = None) -> AppSettings:
        """加载并校验应用配置。"""

        # 文件值与进程环境的合并结果。
        values = _load_env_values(path)
        # 必填数据库地址；显式检查也拒绝空字符串配置。
        database_url = _optional_value(values, "KW_DATABASE_URL")
        if database_url is None:
            raise ValueError("必须通过 KW_DATABASE_URL 提供数据库连接地址")
        # 腾讯云关键变量；全部为空时不启用可选的视频云能力。
        tencent_keys = (
            "KW_TENCENTCLOUD_APP_ID",
            "KW_TENCENTCLOUD_SECRET_ID",
            "KW_TENCENTCLOUD_SECRET_KEY",
            "KW_COS_REGION",
            "KW_COS_BUCKET",
        )
        # 腾讯云配置数据；只要填写任一项就执行完整性校验。
        tencentcloud_data: dict[str, object] | None = None
        if any(_optional_value(values, key) for key in tencent_keys):
            tencentcloud_data = {
                "app_id": values.get("KW_TENCENTCLOUD_APP_ID"),
                "secret_id": values.get("KW_TENCENTCLOUD_SECRET_ID"),
                "secret_key": values.get("KW_TENCENTCLOUD_SECRET_KEY"),
                "cos": {
                    "region": values.get("KW_COS_REGION"),
                    "bucket": values.get("KW_COS_BUCKET"),
                    "prefix": values.get("KW_COS_PREFIX", "knowwhere-temp/"),
                    "presigned_url_ttl_seconds": values.get(
                        "KW_COS_PRESIGNED_URL_TTL_SECONDS", "3600"
                    ),
                },
                "asr": {
                    "engine_model_type": values.get(
                        "KW_TENCENT_ASR_ENGINE_MODEL_TYPE", "16k_zh"
                    ),
                    "poll_interval_seconds": values.get(
                        "KW_TENCENT_ASR_POLL_INTERVAL_SECONDS", "3"
                    ),
                    "max_wait_seconds": values.get(
                        "KW_TENCENT_ASR_MAX_WAIT_SECONDS", "1800"
                    ),
                },
            }
        # 视觉模型变量；任一项存在时要求三项完整。
        vision_keys = ("KW_VISION_API_KEY", "KW_VISION_BASE_URL", "KW_VISION_MODEL")
        # 视觉模型配置数据。
        vision_data: dict[str, object] | None = None
        if any(_optional_value(values, key) for key in vision_keys):
            vision_data = {
                "api_key": values.get("KW_VISION_API_KEY"),
                "base_url": values.get("KW_VISION_BASE_URL"),
                "model": values.get("KW_VISION_MODEL"),
                "timeout_seconds": values.get("KW_VISION_TIMEOUT_SECONDS", "300"),
                "thinking_mode": values.get("KW_VISION_THINKING_MODE", "disabled") or None,
            }
        # 完整配置字典由固定白名单变量构建，不接受任意嵌套供应商名称。
        raw_data = {
            "feishu": {
                "app_id": values.get("KW_FEISHU_APP_ID"),
                "app_secret": values.get("KW_FEISHU_APP_SECRET"),
            },
            "tencentcloud": tencentcloud_data,
            "llm": {
                "api_key": values.get("KW_LLM_API_KEY"),
                "base_url": values.get("KW_LLM_BASE_URL"),
                "model": values.get("KW_LLM_MODEL"),
                "timeout_seconds": values.get("KW_LLM_TIMEOUT_SECONDS", "180"),
                # 缺省时关闭思考模式；显式空值仍允许不发送供应商扩展字段。
                "thinking_mode": values.get("KW_LLM_THINKING_MODE", "disabled") or None,
            },
            "vision": vision_data,
            "douyin": {
                "request_timeout_seconds": values.get(
                    "KW_DOUYIN_REQUEST_TIMEOUT_SECONDS", "60"
                ),
                "max_image_bytes": values.get(
                    "KW_DOUYIN_MAX_IMAGE_BYTES", str(20 * 1024 * 1024)
                ),
                "max_video_bytes": values.get(
                    "KW_DOUYIN_MAX_VIDEO_BYTES", str(4 * 1024 * 1024 * 1024)
                ),
                "max_images": values.get("KW_DOUYIN_MAX_IMAGES", "30"),
                "ffmpeg_path": values.get("KW_FFMPEG_PATH", "ffmpeg"),
            },
            "database_url": database_url,
        }
        return cls.model_validate(raw_data)
