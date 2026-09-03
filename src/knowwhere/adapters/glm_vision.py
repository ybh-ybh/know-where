"""智谱 GLM OpenAI 兼容视觉模型适配器。"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from knowwhere.application.ports import VisionInput, VisionProviderPort
from knowwhere.config import VisionModelSettings


# GLM 视觉模型把有序图片转换为可搜索正文。
class GlmVisionProvider(VisionProviderPort):
    """调用 GLM-4.6V 完成多图 OCR 与语义串联。"""

    # 保存视觉配置和可替换 HTTP 客户端。
    def __init__(
        self,
        settings: VisionModelSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化视觉模型适配器。"""

        # 已校验的模型配置。
        self._settings = settings
        # HTTP 客户端。
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.timeout_seconds)
        )

    # 按作品顺序发送全部图片，避免只理解首图。
    def describe(self, images: tuple[VisionInput, ...], caption: str) -> str:
        """返回完整图文正文。"""

        if not images:
            raise ValueError("图文作品至少需要一张图片")
        # 多模态消息内容，文本提示位于图片之前。
        message_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "你正在整理一条社交平台图文作品。请严格按图片原顺序逐张阅读，"
                    "完整提取可辨识文字，并结合画面关系还原作者表达。输出中文纯文本，"
                    "结构依次为：作品配文、逐图内容（标明第几张）、综合正文。"
                    "不要补写图片和配文没有的信息。\n"
                    f"作品配文：{caption or '无'}"
                ),
            }
        ]
        for image in images:
            if not image.path.is_file() or image.path.stat().st_size == 0:
                raise ValueError("视觉模型本地图片不存在或为空")
            # 当前本地图片 Base64，避免为了公网 URL 强制依赖对象存储。
            image_base64 = base64.b64encode(image.path.read_bytes()).decode("ascii")
            message_content.append(
                {"type": "image_url", "image_url": {"url": image_base64}}
            )
        # OpenAI 兼容请求载荷。
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [{"role": "user", "content": message_content}],
            "max_tokens": 6000,
            "stream": False,
        }
        if self._settings.thinking_mode is not None:
            payload["thinking"] = {"type": self._settings.thinking_mode}
        # Bearer 认证请求头。
        headers = {
            "Authorization": f"Bearer {self._settings.api_key.get_secret_value()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        # 视觉模型响应。
        response = self._client.post(
            f"{self._settings.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        # JSON 响应体。
        response_data = response.json()
        try:
            # 助手消息正文。
            result = response_data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("视觉模型响应缺少 choices[0].message.content") from error
        # 非空文本才可作为图文事实输入。
        normalized_result = str(result or "").strip()
        if not normalized_result:
            raise ValueError("视觉模型返回空正文")
        return normalized_result
