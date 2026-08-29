"""GitHub 仓库 README 提取器测试。"""

from __future__ import annotations

import httpx

from knowwhere.adapters.github import GitHubReadmeExtractor


# 默认分支根目录 README.md 应返回 Markdown 原文和稳定仓库身份。
def test_extract_repository_readme() -> None:
    """验证 GitHub README 的主要提取路径。"""

    # 足够表达标题、列表和代码块结构的 README Markdown。
    readme = "# WeKnora\n\n知识检索与问答框架。\n\n- 功能一\n- 功能二\n\n```bash\nmake run\n```"

    # 固定返回 GitHub README 原文并验证 Raw 内容请求。
    def handler(request: httpx.Request) -> httpx.Response:
        """返回 UTF-8 README。"""

        assert str(request.url) == (
            "https://raw.githubusercontent.com/Tencent/WeKnora/HEAD/README.md"
        )
        return httpx.Response(200, content=readme.encode("utf-8"), request=request)

    # 注入离线客户端。
    client = httpx.Client(transport=httpx.MockTransport(handler))
    # 提取结果。
    content = GitHubReadmeExtractor(client).extract(
        "https://www.github.com/Tencent/WeKnora/?tab=readme-ov-file#readme"
    )

    assert content.canonical_url == "https://github.com/tencent/weknora"
    assert content.platform == "GitHub"
    assert content.platform_content_id == "tencent/weknora"
    assert content.title == "Tencent/WeKnora README"
    assert content.author == "Tencent"
    assert content.published_at is None
    assert content.body_text == readme


# 根目录 README.md 不存在时应使用官方 README API 兼容非标准文件名。
def test_falls_back_to_official_readme_api() -> None:
    """验证 GitHub README 的官方 API 回退路径。"""

    # 非标准 README 原文。
    readme = "WeKnora\n=======\n\nRepository documentation."

    # Raw 地址返回 404，官方 API 返回识别到的 README。
    def handler(request: httpx.Request) -> httpx.Response:
        """按请求地址返回测试响应。"""

        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(404, request=request)
        assert str(request.url) == "https://api.github.com/repos/Tencent/WeKnora/readme"
        assert request.headers["accept"] == "application/vnd.github.raw+json"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        return httpx.Response(200, content=readme.encode("utf-8"), request=request)

    # 注入离线客户端。
    client = httpx.Client(transport=httpx.MockTransport(handler))
    # 提取结果。
    content = GitHubReadmeExtractor(client).extract("https://github.com/Tencent/WeKnora")

    assert content.body_text == readme


# 非仓库主页 URL 必须在发起网络请求前拒绝。
def test_rejects_non_repository_url() -> None:
    """验证 GitHub 来源和路径边界。"""

    # 永远不应被调用的客户端。
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    # 非法链接集合。
    invalid_urls = (
        "http://github.com/Tencent/WeKnora",
        "https://github.com/Tencent/WeKnora/issues/1",
        "https://github.com/Tencent",
        "https://github.com.evil.example/Tencent/WeKnora",
    )
    for invalid_url in invalid_urls:
        try:
            GitHubReadmeExtractor(client).extract(invalid_url)
        except ValueError:
            continue
        raise AssertionError(f"非法 GitHub 仓库链接未被拒绝: {invalid_url}")


# 不存在或没有 README 的仓库必须产生可理解的失败。
def test_reports_missing_readme() -> None:
    """验证 GitHub README 404 错误映射。"""

    # 固定返回 GitHub 未找到响应。
    def handler(request: httpx.Request) -> httpx.Response:
        """返回 404。"""

        return httpx.Response(404, json={"message": "Not Found"}, request=request)

    # 注入离线客户端。
    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        GitHubReadmeExtractor(client).extract("https://github.com/example/missing")
    except ValueError as error:
        assert "README" in str(error)
    else:
        raise AssertionError("缺失 README 的仓库未产生明确错误")
