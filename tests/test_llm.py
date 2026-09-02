"""提示词主约束 LLM 适配器测试。"""

from __future__ import annotations

import json

import httpx

from knowwhere.adapters.llm_openai import PromptFirstOpenAiCompatibleLlm
from knowwhere.config import LlmProviderSettings
from knowwhere.domain.models import ContentQuality, ExtractedContent

# 合法的结构化模型输出。
VALID_PAYLOAD = {
    "short_title": "智能体上下文工程",
    "primary_category": "技术与 AI",
    "category_confidence": 0.91,
    "tags": ["智能体", "上下文工程", "软件开发"],
    "one_sentence_summary": "文章讨论智能体上下文工程。",
    "detailed_summary": "文章系统讨论智能体上下文工程，并给出设计、验证和迭代方面的具体建议。",
    "key_points": ["先定义目标。", "管理上下文。", "持续验证结果。"],
    "content_quality": "full",
    "warnings": [],
}


# 创建测试文章。
def _content() -> ExtractedContent:
    """返回固定内容。"""

    return ExtractedContent(
        source_url="https://mp.weixin.qq.com/s/test",
        canonical_url="https://mp.weixin.qq.com/s/test",
        platform="微信公众号",
        title="智能体上下文工程",
        author="测试作者",
        body_text="正文" * 200,
        published_at=None,
        quality=ContentQuality.FULL,
    )


# 创建带固定响应序列的 LLM 适配器。
def _adapter(responses: list[str]) -> PromptFirstOpenAiCompatibleLlm:
    """组装 Mock HTTP 客户端。"""

    # 待返回消息栈。
    message_stack = iter(responses)

    # OpenAI 兼容响应处理器。
    def handler(request: httpx.Request) -> httpx.Response:
        """返回下一条助手消息。"""

        # 当前助手正文。
        message = next(message_stack)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": message}}]},
            request=request,
        )

    # 测试供应商配置。
    settings = LlmProviderSettings(
        api_key="test-key",
        base_url="https://llm.example/v1",
        model="test-model",
    )
    # 注入 MockTransport，保证测试无网络费用。
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PromptFirstOpenAiCompatibleLlm("test", settings, client)


# 提示词成功时只需一次请求并解析严格 JSON。
def test_direct_json_output() -> None:
    """验证首轮合法 JSON。"""

    # 直接 JSON 响应适配器。
    adapter = _adapter([json.dumps(VALID_PAYLOAD, ensure_ascii=False)])
    # 分析结果。
    result = adapter.analyze(_content(), ("技术与 AI", "其他"))

    assert result.short_title == "智能体上下文工程"
    assert result.primary_category == "技术与 AI"
    assert result.degraded is False


# 混合文本中的代码块 JSON 可以作为兼容解析降级。
def test_fenced_json_output() -> None:
    """验证 Markdown 包裹兼容。"""

    # 带代码块的响应。
    response = f"结果如下：```json\n{json.dumps(VALID_PAYLOAD, ensure_ascii=False)}\n```"
    # 分析结果。
    result = _adapter([response]).analyze(_content(), ("技术与 AI", "其他"))

    assert result.category_confidence == 0.91
    assert result.degraded is False


# 混合文本中的 JSON 字符串可以包含大括号而不破坏平衡对象提取。
def test_balanced_json_output_with_brace_in_string() -> None:
    """验证字符串内的大括号不参与结构计数。"""

    # 包含右大括号字符的合法载荷。
    payload = {**VALID_PAYLOAD, "detailed_summary": "正文包含 } 字符，但 JSON 仍然完整。"}
    # JSON 前后带有模型解释文本。
    response = f"分析结果：{json.dumps(payload, ensure_ascii=False)}，请查收。"
    # 分析结果。
    result = _adapter([response]).analyze(_content(), ("技术与 AI", "其他"))

    assert result.detailed_summary == "正文包含 } 字符，但 JSON 仍然完整。"
    assert result.degraded is False


# 两轮非法输出后必须得到显式启发式降级，而不是非法归档。
def test_invalid_json_twice_uses_explicit_fallback() -> None:
    """验证有界修复和最终降级。"""

    # 两次不满足 JSON Schema 的响应。
    adapter = _adapter(["不是 JSON", '{"primary_category":"不存在的分类"}'])
    # 分析结果。
    result = adapter.analyze(_content(), ("技术与 AI", "其他"))

    assert result.primary_category == "其他"
    assert result.short_title == "智能体上下文工程"
    assert result.degraded is True
    assert "LLM_JSON_DEGRADED" in result.warnings
    assert "AI_TITLE_DEGRADED" in result.warnings


# 超长短标题不满足 Schema，修复失败后必须使用安全兜底。
def test_overlong_short_title_uses_explicit_fallback() -> None:
    """验证短标题最大长度约束。"""

    # 超过 30 个字符的结构化输出。
    invalid_payload = {**VALID_PAYLOAD, "short_title": "过长标题" * 10}
    # 两轮都返回同一个超长标题。
    adapter = _adapter([json.dumps(invalid_payload), json.dumps(invalid_payload)])
    # 降级分析结果。
    result = adapter.analyze(_content(), ("技术与 AI", "其他"))

    assert result.short_title == "智能体上下文工程"
    assert result.degraded is True
