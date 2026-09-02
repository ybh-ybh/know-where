"""微信公众号文章提取适配器。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from knowwhere.application.ports import ContentExtractorPort, ExtractionProgress
from knowwhere.domain.models import ContentQuality, ExtractedContent


# 微信公众号提取器。
class WeChatArticleExtractor(ContentExtractorPort):
    """提取公开微信公众号文章正文。"""

    # 浏览器请求头避免微信返回空壳兼容页。
    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    )

    # 创建可注入 HTTP 客户端。
    def __init__(self, client: httpx.Client | None = None) -> None:
        """初始化提取器。"""

        # HTTP 客户端由适配器独占或外部注入。
        self._client = client or httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": self._USER_AGENT},
        )

    # 下载并解析公开文章。
    def extract(
        self,
        url: str,
        progress: ExtractionProgress | None = None,
    ) -> ExtractedContent:
        """返回清洗后的完整正文。"""

        # 已校验的规范地址。
        canonical_url = self._canonicalize(url)
        # 微信页面响应。
        response = self._client.get(canonical_url)
        response.raise_for_status()
        # 微信部分响应缺少明确 charset，不能使用 HTTPX 的 ISO-8859-1 回退。
        html = response.content.decode("utf-8")
        # HTML 文档树。
        soup = BeautifulSoup(html, "lxml")
        # 正文根节点。
        content_node = soup.select_one("#js_content")
        if content_node is None:
            raise ValueError("微信页面没有 js_content，可能需要验证或文章已不可访问")
        for removable in content_node.select("script, style, noscript, svg, button"):
            removable.decompose()
        # 保留段落边界的正文文本。
        body_text = self._normalize_text(content_node.get_text("\n"))
        if len(body_text) < 200:
            raise ValueError("微信正文过短，拒绝把验证页或空壳页当作文章")
        # OpenGraph 标题优先于页面装饰标题。
        title = self._meta_content(soup, "property", "og:title") or self._text_of(
            soup.select_one("#activity-name")
        )
        if not title:
            raise ValueError("微信文章缺少标题")
        # 作者来自标准 meta 或公众号昵称节点。
        author = self._meta_content(soup, "name", "author") or self._text_of(
            soup.select_one("#js_name")
        )
        # 发布时间来自页面内 ct 秒级时间戳。
        published_at = self._extract_published_at(html)
        return ExtractedContent(
            source_url=url,
            canonical_url=canonical_url,
            platform="微信公众号",
            title=title,
            author=author or None,
            body_text=body_text,
            published_at=published_at,
            quality=ContentQuality.FULL,
            platform_content_id=self._platform_content_id(canonical_url),
        )

    # 校验微信域名并去除片段。
    @staticmethod
    def _canonicalize(url: str) -> str:
        """生成安全规范 URL。"""

        # URL 结构。
        parts = urlsplit(url.strip())
        if parts.scheme != "https" or parts.hostname != "mp.weixin.qq.com":
            raise ValueError("MVP 微信提取器只接受 https://mp.weixin.qq.com 地址")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))

    # 从微信公众号短路径提取稳定文章标识。
    @staticmethod
    def _platform_content_id(url: str) -> str | None:
        """返回微信文章路径中的内容 ID。"""

        # 去除路径首尾斜杠后的分段。
        path_parts = tuple(part for part in urlsplit(url).path.split("/") if part)
        if len(path_parts) == 2 and path_parts[0] == "s":
            return path_parts[1]
        return None

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

    # 压缩空白但保留段落。
    @staticmethod
    def _normalize_text(raw_text: str) -> str:
        """清洗正文空白。"""

        # 清洗后的行集合。
        lines = []
        for raw_line in raw_text.replace("\u200b", "").splitlines():
            # 单行连续空白归一化。
            line = re.sub(r"[\t\r\f\v ]+", " ", raw_line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    # 从页面脚本提取发布时间。
    @staticmethod
    def _extract_published_at(html: str) -> datetime | None:
        """解析微信 ct 时间戳。"""

        # 秒级时间戳匹配。
        match = re.search(r'\bct\s*=\s*["\']?(\d{10})', html)
        if match is None:
            return None
        return datetime.fromtimestamp(int(match.group(1)), tz=UTC)
