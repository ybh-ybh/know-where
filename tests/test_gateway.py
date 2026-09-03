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


# GitHub 仓库主页链接应完整传给 README 提取器。
def test_extract_github_repository_url_from_text_message() -> None:
    """验证 GitHub 仓库链接解析。"""

    # 飞书 text 消息 JSON。
    content = json.dumps(
        {"text": "请解读 [WeKnora](https://github.com/Tencent/WeKnora)。"},
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == "https://github.com/Tencent/WeKnora"


# 抖音分享短链应完整传给解析器。
def test_extract_douyin_short_url_from_text_message() -> None:
    """验证抖音图文或视频短链。"""

    # 飞书文本消息 JSON。
    content = json.dumps(
        {"text": "请归档 https://v.douyin.com/UrtPieUOn5c/。"},
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == "https://v.douyin.com/UrtPieUOn5c/"


# 小红书分享短链应完整传给安全跳转解析器。
def test_extract_xiaohongshu_short_url_from_text_message() -> None:
    """验证小红书图文或视频短链。"""

    # 飞书文本消息 JSON。
    content = json.dumps(
        {"text": "请归档 https://xhslink.cn/o/1uQrX2eif3K。"},
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == "https://xhslink.cn/o/1uQrX2eif3K"


# 小红书标准详情地址应保留访问详情所需的查询参数。
def test_extract_xiaohongshu_detail_url_from_text_message() -> None:
    """验证小红书详情链接。"""

    # 飞书文本消息 JSON。
    content = json.dumps(
        {
            "text": (
                "看看 https://www.xiaohongshu.com/discovery/item/"
                "6a8c08720000000028031d9b?xsec_token=test%3D。"
            )
        },
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == (
        "https://www.xiaohongshu.com/discovery/item/"
        "6a8c08720000000028031d9b?xsec_token=test%3D"
    )


# B站标准视频地址及分P参数应完整传给解析器。
def test_extract_bilibili_video_url_from_text_message() -> None:
    """验证B站标准视频链接。"""

    # 飞书文本消息 JSON。
    content = json.dumps(
        {"text": "请归档 https://www.bilibili.com/video/BV19v8x6uEh8/?p=1。"},
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == (
        "https://www.bilibili.com/video/BV19v8x6uEh8/?p=1"
    )


# B站分享短链应完整传给安全跳转解析器。
def test_extract_bilibili_short_url_from_text_message() -> None:
    """验证 b23.tv 分享短链。"""

    # 飞书文本消息 JSON。
    content = json.dumps(
        {"text": "看看这个 https://b23.tv/Abc_123。"},
        ensure_ascii=False,
    )

    assert extract_supported_url(content) == "https://b23.tv/Abc_123"


# 非法 JSON 和非支持链接都不应触发任务。
def test_ignore_unsupported_message() -> None:
    """验证消息过滤边界。"""

    assert extract_supported_url("not-json") is None
    assert extract_supported_url('{"text":"https://example.com"}') is None
    assert extract_supported_url('{"text":"https://github.com/Tencent/WeKnora/issues/1"}') is None


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
