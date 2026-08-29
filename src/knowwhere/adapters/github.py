"""GitHub 仓库 README 提取适配器。"""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from knowwhere.application.ports import ContentExtractorPort
from knowwhere.domain.models import ContentQuality, ExtractedContent


# GitHub 公开仓库 README 提取器。
class GitHubReadmeExtractor(ContentExtractorPort):
    """通过 GitHub 官方 API 提取公开仓库根目录 README。"""

    # GitHub 用户或组织名的保守格式。
    _OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
    # GitHub 仓库名的保守格式。
    _REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
    # GitHub REST API 的稳定版本。
    _API_VERSION = "2022-11-28"
    # 官方 README 端点的 Markdown 原文媒体类型。
    _RAW_MEDIA_TYPE = "application/vnd.github.raw+json"

    # 创建可注入 HTTP 客户端。
    def __init__(self, client: httpx.Client | None = None) -> None:
        """初始化提取器。"""

        # HTTP 客户端由适配器独占或外部注入。
        self._client = client or httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
        )

    # 下载并返回仓库根目录 README Markdown。
    def extract(self, url: str) -> ExtractedContent:
        """提取公开 GitHub 仓库的 README 原文。"""

        # 已校验的仓库所有者、仓库名与规范地址。
        owner, repository, canonical_url = self._repository_identity(url)
        # 路径分段经过格式校验后再编码进入 Raw 内容地址。
        raw_url = (
            "https://raw.githubusercontent.com/"
            f"{quote(owner, safe='')}/{quote(repository, safe='')}/HEAD/README.md"
        )
        # 默认分支根目录 README.md 响应不消耗 GitHub REST 匿名配额。
        response = self._client.get(
            raw_url,
            headers={"User-Agent": "KnowWhere/0.1"},
        )
        if response.status_code == 404:
            # 官方 README API 作为非标准文件名和位置的兼容回退。
            api_url = (
                "https://api.github.com/repos/"
                f"{quote(owner, safe='')}/{quote(repository, safe='')}/readme"
            )
            response = self._client.get(
                api_url,
                headers={
                    "Accept": self._RAW_MEDIA_TYPE,
                    "X-GitHub-Api-Version": self._API_VERSION,
                    "User-Agent": "KnowWhere/0.1",
                },
            )
        if response.status_code == 404:
            raise ValueError("GitHub 仓库不存在、不是公开仓库或根目录缺少 README")
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError("GitHub API 匿名访问额度已用尽，请稍后重试")
        response.raise_for_status()
        try:
            # GitHub README 原文按 UTF-8 解码，并兼容可选 BOM。
            body_text = response.content.decode("utf-8-sig").strip()
        except UnicodeDecodeError as error:
            raise ValueError("GitHub README 不是有效的 UTF-8 文本") from error
        if not body_text:
            raise ValueError("GitHub 仓库 README 内容为空")
        # 大小写归一化的平台内容 ID 用于跨链接形式识别同一仓库。
        platform_content_id = f"{owner}/{repository}".lower()
        return ExtractedContent(
            source_url=url,
            canonical_url=canonical_url,
            platform="GitHub",
            title=f"{owner}/{repository} README",
            author=owner,
            body_text=body_text,
            published_at=None,
            quality=ContentQuality.FULL,
            platform_content_id=platform_content_id,
        )

    # 校验 GitHub 仓库主页并生成稳定身份。
    @classmethod
    def _repository_identity(cls, url: str) -> tuple[str, str, str]:
        """返回仓库所有者、仓库名和规范 URL。"""

        # URL 结构。
        parts = urlsplit(url.strip())
        # 小写主机名用于精确来源判断。
        hostname = (parts.hostname or "").lower()
        if parts.scheme != "https" or hostname not in {"github.com", "www.github.com"}:
            raise ValueError("GitHub 提取器只接受 https://github.com/所有者/仓库 地址")
        # 仓库主页只能包含所有者和仓库名两个路径分段。
        path_parts = tuple(part for part in parts.path.split("/") if part)
        if len(path_parts) != 2:
            raise ValueError("GitHub 链接必须指向仓库主页，不能指向文件、Issue 或其他页面")
        # 仓库所有者和仓库名保留展示大小写。
        owner, repository = path_parts
        if cls._OWNER_PATTERN.fullmatch(owner) is None:
            raise ValueError("GitHub 链接中的所有者名称无效")
        if cls._REPOSITORY_PATTERN.fullmatch(repository) is None or repository in {".", ".."}:
            raise ValueError("GitHub 链接中的仓库名称无效")
        # GitHub 仓库路径大小写不敏感，统一小写可避免重复归档。
        canonical_path = f"/{owner.lower()}/{repository.lower()}"
        # 分享参数和片段不参与仓库身份。
        canonical_url = urlunsplit(("https", "github.com", canonical_path, "", ""))
        return owner, repository, canonical_url
