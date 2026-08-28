"""稀土掘金文章提取适配器。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from knowwhere.application.ports import ContentExtractorPort
from knowwhere.domain.models import ContentQuality, ExtractedContent


# 稀土掘金文章提取器。
class JuejinArticleExtractor(ContentExtractorPort):
    """从公开 SSR 页面提取稀土掘金文章。"""

    # 浏览器请求头用于获得包含完整正文的 SSR 页面。
    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    )
    # 文章路径只接受数字内容 ID。
    _ARTICLE_PATH_PATTERN = re.compile(r"/post/(\d+)/?")
    # Nuxt SSR 数据中的 Markdown 字符串匹配，不执行远端 JavaScript。
    _MARKDOWN_PATTERN = re.compile(r'mark_content:"((?:\\.|[^"\\])*)"')

    # 创建可注入 HTTP 客户端。
    def __init__(self, client: httpx.Client | None = None) -> None:
        """初始化提取器。"""

        # HTTP 客户端由适配器独占或外部注入。
        self._client = client or httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            headers={
                "User-Agent": self._USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    # 下载并解析公开文章。
    def extract(self, url: str) -> ExtractedContent:
        """返回尽量保留 Markdown 语义的完整正文。"""

        # 已校验且去除分享参数的规范地址。
        canonical_url = self._canonicalize(url)
        # 稀土掘金 SSR 页面响应。
        response = self._client.get(canonical_url)
        response.raise_for_status()
        # 页面固定使用 UTF-8，显式解码避免错误的响应头回退。
        html = response.content.decode("utf-8")
        # HTML 文档树用于读取稳定的可见元数据和正文回退。
        soup = BeautifulSoup(html, "lxml")
        # SSR Markdown 优先保留标题层级、列表和代码块语义。
        body_text = self._extract_markdown(html) or self._extract_dom_body(soup)
        if len(body_text) < 200:
            raise ValueError("掘金正文过短，拒绝把验证页或空壳页当作文章")
        # 页面可见标题比浏览器 title 中的摘要拼接更可靠。
        title = self._text_of(soup.select_one("h1.article-title")) or self._meta_content(
            soup, "property", "og:title"
        )
        if not title:
            raise ValueError("掘金文章缺少标题")
        # 作者来自文章作者区域，避免误取评论用户。
        author = self._text_of(soup.select_one(".author-info-block .author-name .name"))
        # 发布时间来自文章信息区域的 ISO 8601 time 标签。
        published_at = self._extract_published_at(soup)
        return ExtractedContent(
            source_url=url,
            canonical_url=canonical_url,
            platform="掘金",
            title=title,
            author=author or None,
            body_text=body_text,
            published_at=published_at,
            quality=ContentQuality.FULL,
            platform_content_id=self._platform_content_id(canonical_url),
        )

    # 校验掘金域名和文章路径，并丢弃跟踪参数。
    @classmethod
    def _canonicalize(cls, url: str) -> str:
        """生成稳定且安全的规范 URL。"""

        # URL 结构。
        parts = urlsplit(url.strip())
        # 小写主机名用于精确来源判断。
        hostname = (parts.hostname or "").lower()
        if parts.scheme != "https" or hostname not in {"juejin.cn", "www.juejin.cn"}:
            raise ValueError("掘金提取器只接受 https://juejin.cn/post/... 地址")
        # 数字文章 ID 匹配。
        match = cls._ARTICLE_PATH_PATTERN.fullmatch(parts.path)
        if match is None:
            raise ValueError("掘金链接缺少有效的 /post/数字文章ID 路径")
        # 统一主机和路径格式，分享参数不参与内容身份。
        canonical_path = f"/post/{match.group(1)}"
        return urlunsplit(("https", "juejin.cn", canonical_path, "", ""))

    # 从规范路径提取平台文章 ID。
    @classmethod
    def _platform_content_id(cls, url: str) -> str | None:
        """返回稀土掘金文章 ID。"""

        # 规范路径匹配。
        match = cls._ARTICLE_PATH_PATTERN.fullmatch(urlsplit(url).path)
        return match.group(1) if match is not None else None

    # 从 Nuxt SSR JavaScript 中安全解码 Markdown 字符串。
    @classmethod
    def _extract_markdown(cls, html: str) -> str:
        """读取页面内嵌 Markdown，不执行远端脚本。"""

        # mark_content 的 JavaScript 双引号字符串内容。
        match = cls._MARKDOWN_PATTERN.search(html)
        if match is None:
            return ""
        try:
            # JSON 字符串解码兼容换行、Unicode、斜杠和引号转义。
            decoded = json.loads(f'"{match.group(1)}"')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ""
        return cls._normalize_text(str(decoded))

    # 从可见文章 DOM 回退提取正文。
    @classmethod
    def _extract_dom_body(cls, soup: BeautifulSoup) -> str:
        """在 SSR 数据变化时返回可见正文文本。"""

        # 文章正文根节点。
        content_node = soup.select_one("#article-root .article-viewer") or soup.select_one(
            "#article-root"
        )
        if content_node is None:
            return ""
        for removable in content_node.select("script, style, noscript, svg, button"):
            removable.decompose()
        return cls._normalize_text(content_node.get_text("\n"))

    # 读取指定 meta 标签。
    @staticmethod
    def _meta_content(soup: BeautifulSoup, attribute: str, value: str) -> str:
        """读取并清洗 meta content。"""

        # 目标 meta 节点。
        node = soup.find("meta", attrs={attribute: value})
        if node is None:
            return ""
        # content 属性。
        content = node.get("content")
        return str(content).strip() if content else ""

    # 读取节点可见文本。
    @staticmethod
    def _text_of(node: object | None) -> str:
        """安全读取 BeautifulSoup 节点。"""

        if node is None or not hasattr(node, "get_text"):
            return ""
        return str(node.get_text(" ", strip=True)).strip()

    # 压缩行内空白并保留 Markdown 段落分隔。
    @staticmethod
    def _normalize_text(raw_text: str) -> str:
        """清洗正文空白。"""

        # 清洗后的行集合。
        lines: list[str] = []
        # 上一行是否为空，用于压缩连续空行。
        previous_blank = False
        for raw_line in raw_text.replace("\u200b", "").splitlines():
            # 单行连续空白归一化。
            line = re.sub(r"[\t\r\f\v ]+", " ", raw_line).strip()
            if line:
                lines.append(line)
                previous_blank = False
            elif lines and not previous_blank:
                lines.append("")
                previous_blank = True
        return "\n".join(lines).strip()

    # 从文章 time 标签提取发布时间。
    @staticmethod
    def _extract_published_at(soup: BeautifulSoup) -> datetime | None:
        """解析 ISO 8601 发布时间。"""

        # 作者信息区发布时间节点。
        node = soup.select_one(".author-info-block time[datetime]")
        if node is None:
            return None
        # datetime 属性值。
        value = node.get("datetime")
        if not value:
            return None
        try:
            # 统一返回 UTC 感知时间。
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
