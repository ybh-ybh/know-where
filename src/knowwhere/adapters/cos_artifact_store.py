"""腾讯云 COS 私有临时对象适配器。"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from qcloud_cos import CosConfig, CosS3Client

from knowwhere.application.ports import ArtifactStorePort
from knowwhere.config import TencentCloudSettings

# 只允许对象后缀包含安全的 ASCII 字符。
SAFE_SUFFIX_PATTERN = re.compile(r"^\.[a-z0-9]{1,10}$")


# COS 对象存储只暴露不透明 Key 和短时下载地址。
class TencentCosArtifactStore(ArtifactStorePort):
    """在专用前缀中保存并清理私有临时对象。"""

    # 使用共享腾讯云凭据创建单地域客户端。
    def __init__(
        self,
        settings: TencentCloudSettings,
        client: Any | None = None,
    ) -> None:
        """初始化 COS 适配器。"""

        # 腾讯云 COS 子配置。
        self._settings = settings.cos
        # COS SDK 客户端，可由测试 Fake 替换。
        self._client = client or CosS3Client(
            CosConfig(
                Appid=settings.app_id,
                Region=settings.cos.region,
                SecretId=settings.secret_id.get_secret_value(),
                SecretKey=settings.secret_key.get_secret_value(),
                Scheme="https",
            )
        )

    # 上传一个私有临时对象并返回对象 Key。
    def put(self, data: bytes, suffix: str) -> str:
        """保存临时对象。"""

        # 规范化的文件后缀。
        normalized_suffix = suffix.lower()
        if SAFE_SUFFIX_PATTERN.fullmatch(normalized_suffix) is None:
            raise ValueError("临时对象后缀不安全")
        # 按 UTC 日期分区的随机对象 Key。
        date_partition = datetime.now(UTC).strftime("%Y/%m/%d")
        # 最终对象 Key 不包含作品标题或用户数据。
        object_key = (
            f"{self._settings.prefix}{date_partition}/{uuid.uuid4().hex}{normalized_suffix}"
        )
        self._client.put_object(
            Bucket=self._settings.bucket,
            Key=object_key,
            Body=data,
        )
        return object_key

    # 为 ASR 或视觉模型生成短时只读地址。
    def create_download_url(self, artifact_ref: str) -> str:
        """返回限时下载 URL。"""

        self._validate_ref(artifact_ref)
        # SDK 在本地签名，不需要把对象改成公有读。
        return str(
            self._client.get_presigned_url(
                Method="GET",
                Bucket=self._settings.bucket,
                Key=artifact_ref,
                Expired=self._settings.presigned_url_ttl_seconds,
            )
        )

    # 删除临时对象，重复清理保持幂等语义。
    def delete(self, artifact_ref: str) -> None:
        """幂等清理对象。"""

        self._validate_ref(artifact_ref)
        self._client.delete_object(Bucket=self._settings.bucket, Key=artifact_ref)

    # 防止调用方借用适配器操作专用前缀之外的对象。
    def _validate_ref(self, artifact_ref: str) -> None:
        """校验对象引用属于知归专用前缀。"""

        if not artifact_ref.startswith(self._settings.prefix) or ".." in artifact_ref.split("/"):
            raise ValueError("临时对象引用不属于配置前缀")
