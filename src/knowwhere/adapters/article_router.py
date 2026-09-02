"""文章平台提取器分派适配器。"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from knowwhere.application.ports import ContentExtractorPort, ExtractionProgress
from knowwhere.domain.models import ExtractedContent


# 按精确主机名选择文章提取器。
class ArticleExtractorRouter(ContentExtractorPort):
    """把受支持 URL 分派到独立平台适配器。"""

    # 保存不可变语义的主机名到提取器映射。
    def __init__(self, extractors: Mapping[str, ContentExtractorPort]) -> None:
        """初始化平台分派器。"""

        # 标准化后的提取器注册表。
        self._extractors = {
            hostname.lower(): extractor for hostname, extractor in extractors.items()
        }

    # 根据 URL 主机名调用对应提取器。
    def extract(
        self,
        url: str,
        progress: ExtractionProgress | None = None,
    ) -> ExtractedContent:
        """提取一篇已支持平台的文章。"""

        # URL 结构。
        parts = urlsplit(url.strip())
        # 只按精确主机匹配，不能使用不安全的后缀判断。
        hostname = (parts.hostname or "").lower()
        if parts.scheme != "https":
            raise ValueError("文章链接必须使用 HTTPS")
        # 已注册的平台提取器。
        extractor = self._extractors.get(hostname)
        if extractor is None:
            raise ValueError(f"暂不支持该文章平台: {hostname or '缺少主机名'}")
        return extractor.extract(url, progress)
