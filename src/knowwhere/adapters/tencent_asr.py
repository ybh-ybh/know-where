"""腾讯云录音文件识别适配器。"""

from __future__ import annotations

import time
from typing import Any

from tencentcloud.asr.v20190614 import asr_client, models
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)

from knowwhere.application.ports import AsrProviderPort
from knowwhere.config import TencentCloudSettings

# 腾讯云异步任务状态常量来自录音文件识别接口。
ASR_STATUS_SUCCESS = 2
# 腾讯云异步任务失败状态。
ASR_STATUS_FAILED = 3


# 腾讯云文件识别适配器内部完成提交与有界轮询。
class TencentFileAsrProvider(AsrProviderPort):
    """通过私有 COS 短签名 URL 转录标准音频。"""

    # 创建官方 ASR SDK 客户端。
    def __init__(
        self,
        settings: TencentCloudSettings,
        client: Any | None = None,
    ) -> None:
        """初始化 ASR 适配器。"""

        # ASR 轮询与引擎配置。
        self._settings = settings.asr
        # 官方客户端允许测试注入 Fake。
        self._client = client or asr_client.AsrClient(
            credential.Credential(
                settings.secret_id.get_secret_value(),
                settings.secret_key.get_secret_value(),
            ),
            settings.cos.region,
        )

    # 提交 URL 音频并轮询到成功、失败或超时。
    def transcribe(self, artifact_url: str) -> str:
        """返回当前标准音频文本。"""

        if not artifact_url.startswith("https://"):
            raise ValueError("腾讯 ASR 只接收 HTTPS 临时音频地址")
        # 创建识别任务请求。
        create_request = models.CreateRecTaskRequest()
        create_request.EngineModelType = self._settings.engine_model_type
        create_request.ChannelNum = 1
        create_request.ResTextFormat = 0
        create_request.SourceType = 0
        create_request.Url = artifact_url
        # 创建任务响应。
        try:
            # 官方 SDK 创建任务调用。
            create_response = self._client.CreateRecTask(create_request)
        except TencentCloudSDKException as error:
            # 只暴露稳定错误码，不把请求 ID 或可能乱码的供应商消息持久化。
            raise RuntimeError(f"腾讯 ASR 创建任务失败: {error.get_code()}") from error
        # 腾讯云任务 ID 只用于 24 小时内轮询，不作为业务主键。
        task_id = getattr(getattr(create_response, "Data", None), "TaskId", None)
        if task_id is None:
            raise ValueError("腾讯 ASR 创建任务响应缺少 TaskId")
        # 有界轮询截止时间。
        deadline = time.monotonic() + self._settings.max_wait_seconds
        while time.monotonic() < deadline:
            # 查询任务请求。
            status_request = models.DescribeTaskStatusRequest()
            status_request.TaskId = int(task_id)
            # 当前任务状态响应。
            try:
                # 官方 SDK 状态查询调用。
                status_response = self._client.DescribeTaskStatus(status_request)
            except TencentCloudSDKException as error:
                raise RuntimeError(f"腾讯 ASR 查询任务失败: {error.get_code()}") from error
            # 状态数据对象。
            status_data = getattr(status_response, "Data", None)
            # 数字状态。
            status = getattr(status_data, "Status", None)
            if status == ASR_STATUS_SUCCESS:
                # 成功转录正文。
                result = str(getattr(status_data, "Result", "") or "").strip()
                if not result:
                    raise ValueError("腾讯 ASR 成功响应没有转录文本")
                return result
            if status == ASR_STATUS_FAILED:
                # 供应商错误只保存简短消息，响应对象和签名 URL 不进入异常。
                error_message = str(
                    getattr(status_data, "ErrorMsg", "录音文件识别失败")
                )[:500]
                raise RuntimeError(f"腾讯 ASR 识别失败: {error_message}")
            time.sleep(self._settings.poll_interval_seconds)
        raise TimeoutError("腾讯 ASR 识别等待超时")
