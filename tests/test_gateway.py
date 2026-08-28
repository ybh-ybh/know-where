"""飞书消息入口纯函数测试。"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import Mock

from knowwhere.gateway import build_message_handler, extract_supported_url


# 文本消息中第一个微信公众号链接应被识别。
def test_extract_wechat_url_from_text_message() -> None:
    """验证受支持链接解析。"""

    # 飞书 text 消息 JSON。
    content = json.dumps(
        {"text": "请归档 https://mp.weixin.qq.com/s/5VDN-T9K8Wr-DaQ15-I6CA。"},
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == ("https://mp.weixin.qq.com/s/5VDN-T9K8Wr-DaQ15-I6CA")


# 飞书富文本/分享卡片中的 href 也必须被识别。
def test_extract_wechat_url_from_nested_post_message() -> None:
    """验证富文本卡片链接解析。"""

    # 模拟 post 消息的多层内容节点。
    content = json.dumps(
        {
            "zh_cn": {
                "title": "公众号文章",
                "content": [
                    [
                        {
                            "tag": "a",
                            "text": "阅读全文",
                            "href": "https://mp.weixin.qq.com/s/nested-test",
                        }
                    ]
                ],
            }
        },
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == "https://mp.weixin.qq.com/s/nested-test"


# 带分享参数的稀土掘金文章链接应完整传给平台适配器。
def test_extract_juejin_url_from_text_message() -> None:
    """验证稀土掘金链接解析。"""

    # 飞书 text 消息 JSON。
    content = json.dumps(
        {"text": "请归档 https://juejin.cn/post/7671106436446011443?share_token=test。"},
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == (
        "https://juejin.cn/post/7671106436446011443?share_token=test"
    )


# 非法 JSON 和非支持链接都不应触发任务。
def test_ignore_unsupported_message() -> None:
    """验证消息过滤边界。"""

    assert extract_supported_url("not-json") is None
    assert extract_supported_url('{"text":"https://example.com"}') is None


# 消息处理器重构后仍应异步处理且在单进程内按 message_id 去重。
def test_message_handler_processes_supported_message_once() -> None:
    """验证处理器调度和去重行为。"""

    # 飞书工作区授权方法。
    grant_full_access = Mock()
    # 流水线处理结果。
    process = Mock(return_value=SimpleNamespace(record_url="https://feishu.example/record"))
    # 最小运行时替身。
    runtime = SimpleNamespace(
        feishu=SimpleNamespace(grant_full_access=grant_full_access),
        pipeline=SimpleNamespace(process=process),
    )
    # 飞书回复客户端替身。
    reply_client = SimpleNamespace(reply_text=Mock())

    # 同步执行线程目标，便于断言后台分支。
    def thread_factory(
        *,
        target: Callable[..., None],
        args: tuple[str, ...],
        **_: object,
    ) -> Mock:
        """返回启动时立即执行目标的线程替身。"""

        # 线程替身。
        thread = Mock()
        thread.start.side_effect = lambda: target(*args)
        return thread

    # 支持的私聊消息事件。
    event = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_type="p2p",
                message_id="message-12345678",
                message_type="text",
                content=json.dumps({"text": "https://mp.weixin.qq.com/s/test"}),
            ),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="open-id")),
        )
    )
    # 待验证的消息处理器。
    handler = build_message_handler(runtime, reply_client, thread_factory)

    handler(event)
    handler(event)

    grant_full_access.assert_called_once_with("open-id")
    process.assert_called_once_with("https://mp.weixin.qq.com/s/test")
    assert reply_client.reply_text.call_count == 2
