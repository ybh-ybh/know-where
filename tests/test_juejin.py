"""稀土掘金文章提取器测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from knowwhere.adapters.juejin import JuejinArticleExtractor


# SSR Markdown 应优先作为完整正文并保留结构符号。
def test_extract_ssr_markdown_article() -> None:
    """验证掘金公开文章的主要提取路径。"""

    # 足够长且包含标题、列表和代码块的 Markdown 正文。
    markdown = (
        "## 第一节\\n\\n这是正文内容。\\n\\n- 列表一\\n- 列表二\\n\\n"
        '```python\\nprint(\\"hello\\")\\n```\\n\\n' + "补充段落内容。" * 40
    )
    # 模拟掘金 SSR 页面结构和 Nuxt 转义字符串。
    html = f"""
    <html><head><meta property="og:title" content="备用标题"></head><body>
      <h1 class="article-title">一线大厂的 Git 规范</h1>
      <div class="author-info-block">
        <div class="author-name"><span class="name">苏三说技术</span></div>
        <time datetime="2026-08-07T08:07:20.000Z"></time>
      </div>
      <div id="article-root"><div class="article-viewer">DOM 备用正文</div></div>
      <script>window.__NUXT__={{article_info:{{mark_content:"{markdown}"}}}}</script>
    </body></html>
    """

    # 固定返回 SSR 页面的 HTTP 传输。
    def handler(request: httpx.Request) -> httpx.Response:
        """返回 UTF-8 页面并验证规范 URL。"""

        assert str(request.url) == "https://juejin.cn/post/7671106436446011443"
        return httpx.Response(200, content=html.encode("utf-8"), request=request)

    # 注入离线客户端。
    client = httpx.Client(transport=httpx.MockTransport(handler))
    # 提取结果。
    content = JuejinArticleExtractor(client).extract(
        "https://www.juejin.cn/post/7671106436446011443?share_token=test#comment"
    )

    assert content.canonical_url == "https://juejin.cn/post/7671106436446011443"
    assert content.platform == "掘金"
    assert content.platform_content_id == "7671106436446011443"
    assert content.title == "一线大厂的 Git 规范"
    assert content.author == "苏三说技术"
    assert content.published_at == datetime(2026, 8, 7, 8, 7, 20, tzinfo=UTC)
    assert "## 第一节" in content.body_text
    assert "- 列表一" in content.body_text
    assert "```python" in content.body_text


# Nuxt 数据缺失时必须回退到可见文章 DOM。
def test_extract_dom_fallback_article() -> None:
    """验证页面数据结构变化时的正文回退。"""

    # DOM 正文超过空壳判定阈值。
    body = "<p>可见正文段落。</p>" * 50
    # 不包含 mark_content 的 SSR 页面。
    html = f"""
    <html><body>
      <h1 class="article-title">DOM 回退文章</h1>
      <div id="article-root"><div class="article-viewer"><style>bad</style>{body}</div></div>
    </body></html>
    """

    # 固定页面响应。
    def handler(request: httpx.Request) -> httpx.Response:
        """返回只包含 DOM 正文的页面。"""

        return httpx.Response(200, content=html.encode("utf-8"), request=request)

    # 提取结果。
    content = JuejinArticleExtractor(httpx.Client(transport=httpx.MockTransport(handler))).extract(
        "https://juejin.cn/post/1234567890"
    )

    assert content.title == "DOM 回退文章"
    assert "可见正文段落" in content.body_text
    assert "bad" not in content.body_text


# 非文章 URL 必须在发起网络请求前拒绝。
def test_rejects_non_article_url() -> None:
    """验证稀土掘金来源和路径边界。"""

    # 永远不应被调用的客户端。
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    # 非法链接集合。
    invalid_urls = (
        "http://juejin.cn/post/123",
        "https://juejin.cn/user/123",
        "https://juejin.cn.evil.example/post/123",
    )
    for invalid_url in invalid_urls:
        try:
            JuejinArticleExtractor(client).extract(invalid_url)
        except ValueError:
            continue
        raise AssertionError(f"非法掘金链接未被拒绝: {invalid_url}")
