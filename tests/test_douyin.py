"""抖音解析和多媒体分支测试。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from knowwhere.adapters.douyin import (
    DouyinContentExtractor,
    DouyinMediaDownloader,
    DouyinWebResolver,
    DouyinWork,
)
from knowwhere.adapters.local_temp_storage import LocalTempWorkspaceFactory
from knowwhere.application.media import AudioTranscriptionService
from knowwhere.application.ports import AsrTranscription, VisionInput
from knowwhere.config import DouyinSettings, TempStorageSettings
from knowwhere.domain.models import ContentType


# 构造不访问网络的作品详情客户端。
def _resolver_client(
    aweme_id: str,
    detail: dict[str, object],
    detail_forbidden_attempts: int = 0,
) -> httpx.Client:
    """返回模拟短链、ttwid 和详情接口的客户端。"""

    # 已执行的详情请求次数。
    detail_attempts = 0

    # 根据请求路径返回确定性平台响应。
    def handler(request: httpx.Request) -> httpx.Response:
        """模拟抖音公开接口。"""

        nonlocal detail_attempts

        if request.url.host == "v.douyin.com":
            return httpx.Response(
                302,
                headers={"Location": f"https://www.iesdouyin.com/share/note/{aweme_id}/"},
            )
        if request.url.host == "www.iesdouyin.com":
            return httpx.Response(
                302,
                headers={"Location": f"https://www.douyin.com/note/{aweme_id}"},
            )
        if request.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, headers={"Set-Cookie": "ttwid=test-device; Path=/"})
        if request.url.path == "/aweme/v1/web/aweme/detail/":
            detail_attempts += 1
            if detail_attempts <= detail_forbidden_attempts:
                return httpx.Response(403, text="temporary fingerprint rejection")
            assert request.url.params["aweme_id"] == aweme_id
            assert request.url.params["a_bogus"]
            return httpx.Response(200, json={"aweme_detail": detail})
        return httpx.Response(200, text="作品页面")

    # MockTransport 让签名算法和字段解析保持真实执行。
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


# 图文字段优先于 API 中同时出现的零时长 video 占位对象。
def test_resolver_identifies_image_text_and_keeps_all_images() -> None:
    """验证图文类型和有序图片清单。"""

    # 抖音作品 ID。
    aweme_id = "7676403117654641926"
    # 最小图文详情。
    detail: dict[str, object] = {
        "desc": "图文标题\n图文配文",
        "author": {"nickname": "测试作者"},
        "create_time": 1787301884,
        "images": [
            {"url_list": ["https://image.example/1.jpg"]},
            {"url_list": ["https://image.example/2.jpg"]},
        ],
        "video": {"duration": 0, "play_addr": {"url_list": ["https://video.example/x"]}},
    }
    # 使用真实签名逻辑的解析器。
    resolver = DouyinWebResolver(
        DouyinSettings(),
        _resolver_client(aweme_id, detail),
    )

    # 解析结果。
    work = resolver.resolve("https://v.douyin.com/test/")

    assert work.content_type is ContentType.IMAGE_TEXT
    assert work.canonical_url == f"https://www.douyin.com/note/{aweme_id}"
    assert work.image_urls == (
        "https://image.example/1.jpg",
        "https://image.example/2.jpg",
    )
    assert work.video_url is None


# 匿名指纹偶发被拒绝时，应重新注册设备并生成新签名后继续解析。
def test_resolver_retries_transient_detail_forbidden() -> None:
    """验证详情接口瞬时 403 的有限重试。"""

    # 抖音作品 ID。
    aweme_id = "7676403117654641926"
    # 最小图文详情。
    detail: dict[str, object] = {
        "desc": "图文标题",
        "images": [{"url_list": ["https://image.example/1.jpg"]}],
    }
    # 第一次详情请求返回 403 的解析器。
    resolver = DouyinWebResolver(
        DouyinSettings(),
        _resolver_client(aweme_id, detail, detail_forbidden_attempts=1),
    )

    # 重试后的解析结果。
    work = resolver.resolve("https://v.douyin.com/test/")

    assert work.aweme_id == aweme_id
    assert work.content_type is ContentType.IMAGE_TEXT


# Use the reported public video's identity without making network requests.
DOUYIN_TEST_VIDEO_ID = "7703036073350039025"


# Build the public mobile page's embedded JSON instead of executing scripts.
def _share_page_response(items: list[dict[str, object]]) -> httpx.Response:
    """返回包含作品列表的移动分享页。"""

    # Keep the fixture aligned with the observed public page schema.
    state = {"loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": items}}}}
    return httpx.Response(
        200,
        text=f"<html><script>window._ROUTER_DATA = {json.dumps(state)};</script></html>",
    )


# Model the two public sources and reject unexpected requests immediately.
def _fallback_resolver_client(
    api_response: httpx.Response,
    share_response: httpx.Response | httpx.RequestError,
    requests: list[httpx.Request],
) -> httpx.Client:
    """模拟详情失败与分享页后备路径。"""

    # Record each request while keeping the test entirely offline.
    def handler(request: httpx.Request) -> httpx.Response:
        """返回明确配置的响应，禁止隐式访问其他页面。"""

        requests.append(request)
        if request.url.host == "v.douyin.com":
            return httpx.Response(
                302,
                headers={
                    "Location": f"https://www.iesdouyin.com/share/video/{DOUYIN_TEST_VIDEO_ID}/"
                },
            )
        if request.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, headers={"Set-Cookie": "ttwid=anonymous-test; Path=/"})
        if request.url.path == "/aweme/v1/web/aweme/detail/":
            return api_response
        if request.url.host == "www.iesdouyin.com":
            assert request.url.path == f"/share/video/{DOUYIN_TEST_VIDEO_ID}/"
            assert "Mobile" in request.headers["User-Agent"]
            assert "ttwid=anonymous-test" in request.headers["Cookie"]
            if isinstance(share_response, httpx.RequestError):
                raise share_response
            return share_response
        raise AssertionError(
            f"Unexpected public page request: {request.url.host}{request.url.path}"
        )

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


# Recover from denial, empty bodies, malformed JSON, and changed API envelopes.
@pytest.mark.parametrize(
    "api_response",
    [
        httpx.Response(403, text="fingerprint rejected"),
        httpx.Response(200, content=b""),
        httpx.Response(200, text="<html>verification</html>"),
        httpx.Response(200, json={"status_code": 0}),
        httpx.Response(200, json={"aweme_detail": {}}),
        httpx.Response(200, json=[]),
    ],
    ids=["forbidden", "empty", "not-json", "missing-detail", "empty-detail", "non-object"],
)
def test_resolver_falls_back_to_matching_public_share_work(api_response: httpx.Response) -> None:
    """详情接口不可用时仍能提取目标视频，且不会误取推荐作品。"""

    # Place an unrelated recommendation before the requested work.
    items: list[dict[str, object]] = [
        {"aweme_id": "123456", "desc": "无关推荐"},
        {
            "aweme_id": DOUYIN_TEST_VIDEO_ID,
            "desc": 'MoE到底是什么 }; "专家" # Agent',
            "author": {"nickname": "小哲大模型八股"},
            "create_time": 1793530800,
            "video": {
                "duration": 325544,
                "play_addr": {"url_list": ["https://aweme.snssdk.com/aweme/v1/playwm/"]},
            },
        },
    ]
    # Observe network boundaries without inspecting private resolver methods.
    requests: list[httpx.Request] = []
    # Keep the production signature and mapping code active.
    resolver = DouyinWebResolver(
        DouyinSettings(),
        _fallback_resolver_client(api_response, _share_page_response(items), requests),
    )

    # Resolve the exact short link reported by the user.
    work = resolver.resolve("https://v.douyin.com/QiPXAFzL37U/")

    assert work.aweme_id == DOUYIN_TEST_VIDEO_ID
    assert work.title == 'MoE到底是什么 }; "专家" # Agent'
    assert work.author == "小哲大模型八股"
    assert work.content_type is ContentType.VIDEO
    assert work.duration_milliseconds == 325544
    assert work.video_url == "https://aweme.snssdk.com/aweme/v1/playwm/"
    assert work.canonical_url == f"https://www.douyin.com/video/{DOUYIN_TEST_VIDEO_ID}"
    assert sum(request.url.host == "www.iesdouyin.com" for request in requests) == 1
    assert sum(request.url.path == "/aweme/v1/web/aweme/detail/" for request in requests) <= 3


# Preserve the direct API success path without requesting a desktop landing page.
def test_resolver_keeps_video_api_success_without_share_fallback() -> None:
    """成功详情请求不依赖可能被拦截的网页。"""

    # Provide the smallest valid video response from the existing API.
    detail = {
        "aweme_id": DOUYIN_TEST_VIDEO_ID,
        "desc": "可用视频",
        "video": {"play_addr": {"url_list": ["https://video.example/current.mp4"]}},
    }
    # Record requests to verify the fallback was unnecessary.
    requests: list[httpx.Request] = []
    # Make the share source unavailable to catch accidental dependency on it.
    resolver = DouyinWebResolver(
        DouyinSettings(),
        _fallback_resolver_client(
            httpx.Response(200, json={"aweme_detail": detail}),
            httpx.Response(403),
            requests,
        ),
    )

    # A canonical URL already carries all information needed for the API.
    work = resolver.resolve(f"https://www.douyin.com/video/{DOUYIN_TEST_VIDEO_ID}")

    assert work.title == "可用视频"
    assert all(request.url.host != "www.iesdouyin.com" for request in requests)


# Reject unavailable or unrelated share data without exposing request credentials.
@pytest.mark.parametrize(
    "share_response",
    [
        httpx.Response(403, text="token-secret"),
        httpx.Response(302, headers={"Location": "https://untrusted.example/?token=token-secret"}),
        httpx.Response(200, text="<html>verification token-secret</html>"),
        httpx.Response(200, text="<script>window._ROUTER_DATA = {invalid};</script>"),
        _share_page_response([{"aweme_id": "999999", "desc": "无关作品"}]),
        _share_page_response([]),
        httpx.ReadTimeout("request failed with token-secret"),
    ],
    ids=["forbidden", "redirect", "no-state", "invalid-json", "wrong-work", "empty", "timeout"],
)
def test_resolver_rejects_unusable_share_data_safely(
    share_response: httpx.Response | httpx.RequestError,
) -> None:
    """后备失败不会当作成功或向回执泄漏 URL、Cookie。"""

    # Record requests to detect accidental cross-host redirect following.
    requests: list[httpx.Request] = []
    # Include an unavailable API so every case exercises the fallback boundary.
    resolver = DouyinWebResolver(
        DouyinSettings(),
        _fallback_resolver_client(httpx.Response(403), share_response, requests),
    )

    # User-facing errors must carry only safe explanatory text.
    with pytest.raises(ValueError) as caught:
        resolver.resolve("https://v.douyin.com/QiPXAFzL37U/")

    assert "抖音" in str(caught.value)
    assert "https://" not in str(caught.value)
    assert "token-secret" not in str(caught.value)
    assert "anonymous-test" not in str(caught.value)
    assert "a_bogus" not in str(caught.value)
    assert all(request.url.host != "untrusted.example" for request in requests)


# Keep the short-link host allowlist intact before any detail lookup.
def test_resolver_rejects_cross_host_short_link_redirect() -> None:
    """不跟随短链指向的非抖音主机。"""

    # Track whether a redirect target was requested.
    requested_hosts: list[str] = []

    # Respond only to the initial short-link request.
    def handler(request: httpx.Request) -> httpx.Response:
        """模拟不可信的跳转目标。"""

        requested_hosts.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://untrusted.example/video/123"})

    # Even a client configured to follow redirects must obey the resolver boundary.
    resolver = DouyinWebResolver(
        DouyinSettings(),
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )

    with pytest.raises(ValueError):
        resolver.resolve("https://v.douyin.com/QiPXAFzL37U/")

    assert requested_hosts == ["v.douyin.com"]


# 记录 COS 生命周期的 Fake。
class FakeArtifactStore:
    """保存上传和删除记录。"""

    # 初始化内存状态。
    def __init__(self) -> None:
        """初始化 Fake。"""

        # 上传对象引用。
        self.put_refs: list[str] = []
        # 删除对象引用。
        self.deleted_refs: list[str] = []

    # 保存对象并返回递增引用。
    def put(self, data: bytes, suffix: str) -> str:
        """模拟上传。"""

        assert data
        # 当前对象引用。
        artifact_ref = f"knowwhere-temp/{len(self.put_refs)}{suffix}"
        self.put_refs.append(artifact_ref)
        return artifact_ref

    # 生成测试短时 URL。
    def create_download_url(self, artifact_ref: str) -> str:
        """模拟签名。"""

        return f"https://cos.example/{artifact_ref}?signature=test"

    # 记录清理对象。
    def delete(self, artifact_ref: str) -> None:
        """模拟删除。"""

        self.deleted_refs.append(artifact_ref)


# 把预置作品作为解析结果返回。
class FakeResolver:
    """固定作品解析器。"""

    # 保存作品。
    def __init__(self, work: DouyinWork) -> None:
        """初始化 Fake。"""

        self._work = work

    # 忽略地址返回预置作品。
    def resolve(self, url: str) -> DouyinWork:
        """返回固定作品。"""

        return self._work


# 写入假媒体内容。
class FakeDownloader(DouyinMediaDownloader):
    """避免访问 CDN。"""

    # 不初始化真实 HTTP 客户端。
    def __init__(self) -> None:
        """初始化 Fake。"""

    # 写入确定性媒体字节。
    def download(self, url: str, destination: Path, max_bytes: int) -> str:
        """模拟媒体下载。"""

        destination.write_bytes(b"media")
        return "image/jpeg" if "image" in url else "video/mp4"


# 记录视觉模型收到的图片顺序。
class FakeVision:
    """固定视觉模型。"""

    # 初始化调用记录。
    def __init__(self) -> None:
        """初始化 Fake。"""

        # 最近图片文件名。
        self.image_names: tuple[str, ...] = ()

    # 返回固定图文正文。
    def describe(self, images: tuple[VisionInput, ...], caption: str) -> str:
        """模拟多图理解。"""

        assert all(image.path.read_bytes() == b"media" for image in images)
        # 保留可在工作区清理后断言的文件名。
        self.image_names = tuple(image.path.name for image in images)
        return f"配文：{caption}\n两张图片的完整 OCR"


# 写入假标准音频。
class FakeAudioExtractor:
    """避免调用 FFmpeg。"""

    # 生成最小音频字节。
    def extract_segments(
        self,
        video_path: Path,
        output_directory: Path,
    ) -> tuple[Path, ...]:
        """模拟音频提取。"""

        assert video_path.read_bytes() == b"media"
        # 两个测试音频分段路径。
        audio_paths = (
            output_directory / "audio-0000.mp3",
            output_directory / "audio-0001.mp3",
        )
        for audio_path in audio_paths:
            audio_path.write_bytes(b"audio")
        return audio_paths


# ASR Fake 返回固定文本。
class FakeAsr:
    """固定转录供应商。"""

    # 初始化调用记录。
    def __init__(self) -> None:
        """初始化 Fake。"""

        # 已转录分段数量。
        self.calls = 0

    # 验证输入是本地标准音频。
    def transcribe(self, audio_path: Path) -> AsrTranscription:
        """模拟视频转录。"""

        assert audio_path.read_bytes() == b"audio"
        self.calls += 1
        return AsrTranscription(text="这是完整视频转录。")


# 图文链路必须上传、理解并清理全部图片。
def test_image_text_extractor_processes_and_deletes_every_image(tmp_path: Path) -> None:
    """验证图文媒体生命周期。"""

    # 图文工作对象。
    work = DouyinWork(
        source_url="https://v.douyin.com/image/",
        canonical_url="https://www.douyin.com/note/1",
        aweme_id="1",
        content_type=ContentType.IMAGE_TEXT,
        title="图文",
        author="作者",
        caption="配文",
        published_at=None,
        image_urls=("https://image.example/1", "https://image.example/2"),
    )
    # 视觉 Fake。
    vision = FakeVision()
    # 受控本地临时目录。
    workspace_root = tmp_path / "temp"
    # 被测提取器。
    extractor = DouyinContentExtractor(
        settings=DouyinSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),
        transcriber=AudioTranscriptionService(FakeAsr()),
        vision=vision,
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
        temp_workspaces=LocalTempWorkspaceFactory(
            TempStorageSettings(local_root=workspace_root)
        ),
    )

    # 领域提取结果。
    content = extractor.extract(work.source_url)

    assert content.content_type is ContentType.IMAGE_TEXT
    assert "完整 OCR" in content.body_text
    assert vision.image_names == ("image-0.bin", "image-1.bin")
    assert list(workspace_root.iterdir()) == []


# 视频链路必须把配文与 ASR 转录合并，并清理音频对象。
def test_video_extractor_transcribes_and_deletes_audio(tmp_path: Path) -> None:
    """验证视频媒体生命周期。"""

    # 视频工作对象。
    work = DouyinWork(
        source_url="https://v.douyin.com/video/",
        canonical_url="https://www.douyin.com/video/2",
        aweme_id="2",
        content_type=ContentType.VIDEO,
        title="视频",
        author="作者",
        caption="视频配文",
        published_at=None,
        video_url="https://video.example/2",
    )
    # 记录本地 ASR 调用。
    asr = FakeAsr()
    # 受控本地临时目录。
    workspace_root = tmp_path / "temp"
    # 被测提取器。
    extractor = DouyinContentExtractor(
        settings=DouyinSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),
        transcriber=AudioTranscriptionService(asr),
        vision=None,
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
        temp_workspaces=LocalTempWorkspaceFactory(
            TempStorageSettings(local_root=workspace_root)
        ),
    )

    # 领域提取结果。
    content = extractor.extract(work.source_url)

    assert content.content_type is ContentType.VIDEO
    assert "视频配文" in content.body_text
    assert content.body_text.count("完整视频转录") == 2
    assert asr.calls == 2
    assert list(workspace_root.iterdir()) == []
