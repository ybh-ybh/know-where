"""微信公众号提取器测试。"""

from __future__ import annotations

import httpx

from knowwhere.adapters.wechat import WeChatArticleExtractor


# 无 charset 的微信响应仍必须按 UTF-8 正确解析中文。
def test_extract_utf8_article_without_charset() -> None:
    """验证微信常见编码响应。"""

    # 伪造的最小微信 HTML。
    html = (
        """
    <html><head>
      <meta property="og:title" content="一篇中文测试文章">
      <meta name="author" content="测试公众号">
    </head><body>
      <div id="js_content"><p>这是完整正文。</p><p>用于验证中文编码。</p>
    """
        + ("正文补充内容。" * 50)
        + "</div></body></html>"
    )

    # 固定返回无 charset 响应的 HTTP 传输。
    def handler(request: httpx.Request) -> httpx.Response:
        """返回 UTF-8 字节。"""

        return httpx.Response(200, content=html.encode("utf-8"), request=request)

    # 注入离线客户端。
    client = httpx.Client(transport=httpx.MockTransport(handler))
    # 提取结果。
    content = WeChatArticleExtractor(client).extract("https://mp.weixin.qq.com/s/test")

    assert content.title == "一篇中文测试文章"
    assert content.author == "测试公众号"
    assert content.platform_content_id == "test"
    assert "用于验证中文编码" in content.body_text


# 非微信 URL 必须在网络请求前拒绝。
def test_rejects_non_wechat_domain() -> None:
    """验证来源边界。"""

    # 永远不应被调用的客户端。
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    try:
        WeChatArticleExtractor(client).extract("https://example.com/article")
    except ValueError as error:
        assert "mp.weixin.qq.com" in str(error)
    else:
        raise AssertionError("非微信域名未被拒绝")
