"""视觉、COS 与腾讯 ASR 适配器契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx

from knowwhere.adapters.cos_artifact_store import TencentCosArtifactStore
from knowwhere.adapters.glm_vision import GlmVisionProvider
from knowwhere.adapters.tencent_asr import TencentFileAsrProvider
from knowwhere.config import TencentCloudSettings, VisionModelSettings


# 记录 COS SDK 调用参数。
class FakeCosClient:
    """最小 COS SDK Fake。"""

    # 初始化调用记录。
    def __init__(self) -> None:
        """初始化 Fake。"""

        # 上传参数。
        self.put_kwargs: dict[str, Any] = {}
        # 删除参数。
        self.delete_kwargs: dict[str, Any] = {}

    # 保存上传参数。
    def put_object(self, **kwargs: Any) -> None:
        """模拟 put_object。"""

        self.put_kwargs = kwargs

    # 返回确定性短签名 URL。
    def get_presigned_url(self, **kwargs: Any) -> str:
        """模拟本地签名。"""

        return f"https://cos.example/{kwargs['Key']}?ttl={kwargs['Expired']}"

    # 保存删除参数。
    def delete_object(self, **kwargs: Any) -> None:
        """模拟 delete_object。"""

        self.delete_kwargs = kwargs


# 创建测试腾讯云配置。
def _tencent_settings() -> TencentCloudSettings:
    """返回无真实凭据配置。"""

    return TencentCloudSettings.model_validate(
        {
            "app_id": "1234567890",
            "secret_id": "test-id",
            "secret_key": "test-key",
            "cos": {
                "region": "ap-shanghai",
                "bucket": "test-1234567890",
                "prefix": "knowwhere-temp/",
                "presigned_url_ttl_seconds": 600,
            },
            "asr": {"poll_interval_seconds": 0.5, "max_wait_seconds": 30},
        }
    )


# COS 对象必须落在专用前缀并支持签名和删除。
def test_cos_artifact_store_private_lifecycle() -> None:
    """验证 COS 临时对象生命周期。"""

    # COS Fake。
    client = FakeCosClient()
    # 被测存储适配器。
    store = TencentCosArtifactStore(_tencent_settings(), client)

    # 上传对象引用。
    artifact_ref = store.put(b"image", ".jpg")
    # 短时 URL。
    download_url = store.create_download_url(artifact_ref)
    store.delete(artifact_ref)

    assert artifact_ref.startswith("knowwhere-temp/")
    assert client.put_kwargs["Body"] == b"image"
    assert "ttl=600" in download_url
    assert client.delete_kwargs["Key"] == artifact_ref


# 视觉请求必须保留图片顺序并读取 OpenAI 兼容响应。
def test_glm_vision_sends_all_images_in_order() -> None:
    """验证多图视觉请求。"""

    # 捕获的请求体。
    captured_payload: dict[str, Any] = {}

    # 模拟 GLM 响应。
    def handler(request: httpx.Request) -> httpx.Response:
        """记录请求并返回视觉正文。"""

        import json

        captured_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "完整图文正文"}}]},
        )

    # 视觉配置。
    settings = VisionModelSettings.model_validate(
        {
            "api_key": "vision-key",
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "model": "GLM-4.6V",
        }
    )
    # MockTransport 客户端。
    client = httpx.Client(transport=httpx.MockTransport(handler))
    # 被测视觉适配器。
    provider = GlmVisionProvider(settings, client)

    # 视觉正文。
    result = provider.describe(("https://cos/1", "https://cos/2"), "配文")

    # 多模态内容列表。
    content = captured_payload["messages"][0]["content"]
    assert settings.model == "glm-4.6v"
    assert [item["image_url"]["url"] for item in content[1:]] == [
        "https://cos/1",
        "https://cos/2",
    ]
    assert result == "完整图文正文"


# 腾讯 ASR Fake 按官方响应对象形状返回一次成功。
class FakeAsrClient:
    """最小腾讯 ASR SDK Fake。"""

    # 初始化请求记录。
    def __init__(self) -> None:
        """初始化 Fake。"""

        # 创建任务请求。
        self.create_request: Any = None
        # 查询状态请求。
        self.status_request: Any = None

    # 返回任务 ID。
    def CreateRecTask(self, request: Any) -> SimpleNamespace:
        """模拟创建任务。"""

        self.create_request = request
        return SimpleNamespace(Data=SimpleNamespace(TaskId=123))

    # 返回成功转录。
    def DescribeTaskStatus(self, request: Any) -> SimpleNamespace:
        """模拟查询任务。"""

        self.status_request = request
        return SimpleNamespace(Data=SimpleNamespace(Status=2, Result="转录成功"))


# ASR 必须使用 URL 模式、单声道和配置引擎。
def test_tencent_asr_submits_url_and_returns_result() -> None:
    """验证腾讯 ASR 提交和轮询契约。"""

    # ASR Fake。
    client = FakeAsrClient()
    # 被测 ASR 适配器。
    provider = TencentFileAsrProvider(_tencent_settings(), client)

    # 转录结果。
    result = provider.transcribe("https://cos.example/audio.mp3?signature=test")

    assert client.create_request.SourceType == 0
    assert client.create_request.ChannelNum == 1
    assert client.create_request.EngineModelType == "16k_zh"
    assert client.status_request.TaskId == 123
    assert result == "转录成功"
