"""抖音解析和多媒体分支测试。"""

from __future__ import annotations

from pathlib import Path

import httpx

from knowwhere.adapters.douyin import (
    DouyinContentExtractor,
    DouyinMediaDownloader,
    DouyinWebResolver,
    DouyinWork,
)
from knowwhere.config import DouyinSettings
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

        # 最近图片地址。
        self.image_urls: tuple[str, ...] = ()

    # 返回固定图文正文。
    def describe(self, image_urls: tuple[str, ...], caption: str) -> str:
        """模拟多图理解。"""

        self.image_urls = image_urls
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

    # 验证输入是短时 HTTPS URL。
    def transcribe(self, artifact_url: str) -> str:
        """模拟视频转录。"""

        assert artifact_url.startswith("https://cos.example/")
        return "这是完整视频转录。"


# 图文链路必须上传、理解并清理全部图片。
def test_image_text_extractor_processes_and_deletes_every_image() -> None:
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
    # COS Fake。
    store = FakeArtifactStore()
    # 视觉 Fake。
    vision = FakeVision()
    # 被测提取器。
    extractor = DouyinContentExtractor(
        settings=DouyinSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),
        artifact_store=store,
        asr=FakeAsr(),
        vision=vision,
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
    )

    # 领域提取结果。
    content = extractor.extract(work.source_url)

    assert content.content_type is ContentType.IMAGE_TEXT
    assert "完整 OCR" in content.body_text
    assert len(vision.image_urls) == 2
    assert store.deleted_refs == store.put_refs


# 视频链路必须把配文与 ASR 转录合并，并清理音频对象。
def test_video_extractor_transcribes_and_deletes_audio() -> None:
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
    # COS Fake。
    store = FakeArtifactStore()
    # 被测提取器。
    extractor = DouyinContentExtractor(
        settings=DouyinSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),
        artifact_store=store,
        asr=FakeAsr(),
        vision=None,
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
    )

    # 领域提取结果。
    content = extractor.extract(work.source_url)

    assert content.content_type is ContentType.VIDEO
    assert "视频配文" in content.body_text
    assert content.body_text.count("完整视频转录") == 2
    assert len(store.put_refs) == 2
    assert store.deleted_refs == store.put_refs
