"""小红书公开页面解析和多媒体分支测试。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from knowwhere.adapters.xiaohongshu import (
    XiaohongshuContentExtractor,
    XiaohongshuMediaDownloader,
    XiaohongshuWebResolver,
    XiaohongshuWork,
)
from knowwhere.config import XiaohongshuSettings
from knowwhere.domain.models import ContentType


# 构造包含小红书页面初始状态的最小 HTML。
def _detail_html(note_id: str, note: dict[str, object]) -> str:
    """返回可由真实解析逻辑读取的详情页。"""

    # 页面状态保留一个 JavaScript undefined，验证兼容替换范围。
    state = {
        "note": {
            "noteDetailMap": {
                note_id: {"note": note, "loading": "undefined_marker"},
            }
        }
    }
    # JSON 文本中的占位字符串转为 JavaScript 值。
    state_text = json.dumps(state, ensure_ascii=False).replace('"undefined_marker"', "undefined")
    return f"<html><script>window.__INITIAL_STATE__={state_text}</script></html>"


# 构造不访问网络的短链和详情页客户端。
def _resolver_client(note_id: str, note: dict[str, object]) -> httpx.Client:
    """返回模拟小红书安全跳转和公开页面的客户端。"""

    # 根据请求主机返回确定性响应。
    def handler(request: httpx.Request) -> httpx.Response:
        """模拟小红书短链和详情页。"""

        if request.url.host == "xhslink.cn":
            return httpx.Response(
                302,
                headers={
                    "Location": (
                        f"https://www.xiaohongshu.com/discovery/item/{note_id}"
                        "?xsec_token=test-token&xsec_source=app_share"
                    )
                },
            )
        if request.url.host == "www.xiaohongshu.com":
            return httpx.Response(200, text=_detail_html(note_id, note))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


# 图文解析必须保留全部图片的原始顺序并移除规范链接中的令牌。
def test_resolver_identifies_image_text_and_keeps_all_images() -> None:
    """验证公开图文页面解析。"""

    # 小红书图文笔记 ID。
    note_id = "6a8c08720000000028031d9b"
    # 最小图文笔记字段。
    note: dict[str, object] = {
        "noteId": note_id,
        "type": "normal",
        "title": "图文标题",
        "desc": "图文配文",
        "time": 1_787_648_821_000,
        "user": {"nickname": "测试作者"},
        "imageList": [
            {
                "urlDefault": "http://sns-webpic-qc.xhscdn.com/path/1!format/jpg",
            },
            {
                "infoList": [
                    {
                        "imageScene": "WB_DFT",
                        "url": "http://sns-webpic-qc.xhscdn.com/path/2!format/jpg",
                    }
                ]
            },
        ],
    }
    # 使用真实页面状态解析逻辑的解析器。
    resolver = XiaohongshuWebResolver(
        XiaohongshuSettings(),
        _resolver_client(note_id, note),
    )

    # 解析后的工作对象。
    work = resolver.resolve("https://xhslink.cn/o/image-test")

    assert work.content_type is ContentType.IMAGE_TEXT
    assert work.note_id == note_id
    assert work.canonical_url == f"https://www.xiaohongshu.com/explore/{note_id}"
    assert work.author == "测试作者"
    assert work.image_urls == (
        "https://sns-webpic-qc.xhscdn.com/path/1!format/jpg",
        "https://sns-webpic-qc.xhscdn.com/path/2!format/jpg",
    )


# 视频解析必须选择最高质量流并保留同流备用 CDN。
def test_resolver_identifies_video_and_selects_best_stream() -> None:
    """验证公开视频字段和候选地址。"""

    # 小红书视频笔记 ID。
    note_id = "6a422e3f000000002201a9ce"
    # 最小视频笔记字段。
    note: dict[str, object] = {
        "noteId": note_id,
        "type": "video",
        "title": "视频标题",
        "desc": "视频配文",
        "user": {"nickname": "视频作者"},
        "imageList": [{"urlDefault": "http://sns-webpic-qc.xhscdn.com/cover"}],
        "video": {
            "media": {
                "stream": {
                    "h264": [
                        {
                            "width": 720,
                            "height": 960,
                            "videoBitrate": 500_000,
                            "size": 8_000_000,
                            "videoDuration": 113_566,
                            "masterUrl": "http://sns-video-v3.xhscdn.com/high.mp4?sign=x",
                            "backupUrls": [
                                "http://sns-bak-v1.xhscdn.com/high.mp4",
                                "http://sns-bak-v6.xhscdn.com/high.mp4",
                            ],
                        }
                    ],
                    "h265": [
                        {
                            "width": 360,
                            "height": 480,
                            "videoBitrate": 200_000,
                            "masterUrl": "http://sns-video-v3.xhscdn.com/low.mp4",
                        }
                    ],
                }
            }
        },
    }
    # 使用真实页面状态解析逻辑的解析器。
    resolver = XiaohongshuWebResolver(
        XiaohongshuSettings(),
        _resolver_client(note_id, note),
    )

    # 解析后的视频工作对象。
    work = resolver.resolve("https://xhslink.cn/o/video-test")

    assert work.content_type is ContentType.VIDEO
    assert work.video_urls == (
        "https://sns-video-v3.xhscdn.com/high.mp4?sign=x",
        "https://sns-bak-v1.xhscdn.com/high.mp4",
        "https://sns-bak-v6.xhscdn.com/high.mp4",
    )
    assert work.duration_milliseconds == 113_566


# 分享短链不得把服务端请求跳转到非官方主机。
def test_resolver_rejects_non_official_redirect_target() -> None:
    """验证短链跳转 SSRF 边界。"""

    # 把小红书短链指向非官方地址的模拟客户端。
    def handler(_request: httpx.Request) -> httpx.Response:
        """返回恶意跳转。"""

        return httpx.Response(302, headers={"Location": "https://example.com/private"})

    # 使用恶意跳转客户端的解析器。
    resolver = XiaohongshuWebResolver(
        XiaohongshuSettings(),
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )

    with pytest.raises(ValueError, match="官方 HTTPS 主机"):
        resolver.resolve("https://xhslink.cn/o/unsafe")


# 下载器必须在首选 CDN 失败时尝试备用地址。
def test_media_downloader_falls_back_to_backup_url(tmp_path: Path) -> None:
    """验证视频 CDN 候选回退。"""

    # 根据主机模拟首选失败和备用成功。
    def handler(request: httpx.Request) -> httpx.Response:
        """返回确定性媒体响应。"""

        if request.url.host == "sns-video-v3.xhscdn.com":
            return httpx.Response(503)
        return httpx.Response(200, content=b"video", headers={"Content-Type": "video/mp4"})

    # 使用模拟 CDN 的媒体下载器。
    downloader = XiaohongshuMediaDownloader(
        XiaohongshuSettings(),
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    # 本地下载目标。
    destination = tmp_path / "video.mp4"

    # 实际响应类型。
    content_type = downloader.download(
        (
            "https://sns-video-v3.xhscdn.com/video.mp4",
            "https://sns-bak-v1.xhscdn.com/video.mp4",
        ),
        destination,
        1024,
    )

    assert content_type == "video/mp4"
    assert destination.read_bytes() == b"video"


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


# 把预置笔记作为解析结果返回。
class FakeResolver:
    """固定小红书工作对象。"""

    # 保存工作对象。
    def __init__(self, work: XiaohongshuWork) -> None:
        """初始化 Fake。"""

        self._work = work

    # 忽略地址并返回预置作品。
    def resolve(self, url: str) -> XiaohongshuWork:
        """返回固定解析结果。"""

        return self._work


# 写入假媒体内容。
class FakeDownloader:
    """避免访问小红书 CDN。"""

    # 写入确定性媒体字节。
    def download(self, urls: tuple[str, ...], destination: Path, max_bytes: int) -> str:
        """模拟图片或视频下载。"""

        assert urls
        assert max_bytes > 0
        destination.write_bytes(b"media")
        return "image/jpeg" if "image" in urls[0] else "video/mp4"


# 记录视觉模型收到的图片顺序。
class FakeVision:
    """固定视觉模型响应。"""

    # 初始化调用记录。
    def __init__(self) -> None:
        """初始化 Fake。"""

        # 最近图片地址。
        self.image_urls: tuple[str, ...] = ()

    # 返回固定图文正文。
    def describe(self, image_urls: tuple[str, ...], caption: str) -> str:
        """模拟多图理解。"""

        self.image_urls = image_urls
        return f"配文：{caption}\n全部图片的完整 OCR"


# 写入假标准音频。
class FakeAudioExtractor:
    """避免调用 FFmpeg。"""

    # 生成两个最小音频分段。
    def extract_segments(
        self,
        video_path: Path,
        output_directory: Path,
    ) -> tuple[Path, ...]:
        """模拟音频提取。"""

        assert video_path.read_bytes() == b"media"
        # 有序测试音频路径。
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
    """验证小红书图文媒体生命周期。"""

    # 图文工作对象。
    work = XiaohongshuWork(
        source_url="https://xhslink.cn/o/image",
        canonical_url="https://www.xiaohongshu.com/explore/000000000000000000000001",
        note_id="000000000000000000000001",
        content_type=ContentType.IMAGE_TEXT,
        title="图文",
        author="作者",
        caption="配文",
        published_at=None,
        image_urls=(
            "https://sns-webpic-qc.xhscdn.com/image-1",
            "https://sns-webpic-qc.xhscdn.com/image-2",
        ),
    )
    # COS Fake。
    store = FakeArtifactStore()
    # 视觉 Fake。
    vision = FakeVision()
    # 被测提取器。
    extractor = XiaohongshuContentExtractor(
        settings=XiaohongshuSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        artifact_store=store,
        asr=FakeAsr(),
        vision=vision,
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
    )

    # 领域提取结果。
    content = extractor.extract(work.source_url)

    assert content.platform == "小红书"
    assert content.content_type is ContentType.IMAGE_TEXT
    assert "完整 OCR" in content.body_text
    assert len(vision.image_urls) == 2
    assert store.deleted_refs == store.put_refs


# 视频链路必须合并配文与全部分段转录并清理音频对象。
def test_video_extractor_transcribes_and_deletes_audio() -> None:
    """验证小红书视频媒体生命周期。"""

    # 视频工作对象。
    work = XiaohongshuWork(
        source_url="https://xhslink.cn/o/video",
        canonical_url="https://www.xiaohongshu.com/explore/000000000000000000000002",
        note_id="000000000000000000000002",
        content_type=ContentType.VIDEO,
        title="视频",
        author="作者",
        caption="视频配文",
        published_at=None,
        video_urls=("https://sns-video-v3.xhscdn.com/video.mp4",),
    )
    # COS Fake。
    store = FakeArtifactStore()
    # 被测提取器。
    extractor = XiaohongshuContentExtractor(
        settings=XiaohongshuSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),  # type: ignore[arg-type]
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
