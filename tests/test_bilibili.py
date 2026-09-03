"""B站公开视频解析和转录分支测试。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from knowwhere.adapters.bilibili import (
    BilibiliContentExtractor,
    BilibiliMediaDownloader,
    BilibiliMediaUnavailableError,
    BilibiliWebResolver,
    BilibiliWork,
)
from knowwhere.adapters.local_temp_storage import LocalTempWorkspaceFactory
from knowwhere.application.media import AudioTranscriptionService
from knowwhere.application.ports import AsrTranscription
from knowwhere.config import BilibiliSettings, TempStorageSettings
from knowwhere.domain.models import ContentQuality, ContentType


# 构造不访问网络的B站公开接口客户端。
def _resolver_client(bvid: str, multi_page: bool = True) -> httpx.Client:
    """返回模拟短链、视频详情和 DASH 播放信息的客户端。"""

    # 按测试场景返回单P或多P详情。
    pages = [
        {"cid": 1001, "page": 1, "part": "第一集", "duration": 300},
        {"cid": 1002, "page": 2, "part": "第二集", "duration": 391},
    ]
    if not multi_page:
        pages = [{"cid": 1001, "page": 1, "part": "测试合集", "duration": 691}]

    # 根据请求路径返回确定性平台响应。
    def handler(request: httpx.Request) -> httpx.Response:
        """模拟B站公开页面和接口。"""

        if request.url.host == "b23.tv":
            return httpx.Response(
                302,
                headers={
                    "Location": f"https://www.bilibili.com/video/{bvid}/?p=2&share_source=test"
                },
            )
        if request.url.path == "/x/web-interface/view":
            assert request.url.params["bvid"] == bvid
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": bvid,
                        "aid": 123,
                        "title": "测试合集",
                        "desc": "完整视频简介",
                        "owner": {"name": "测试作者"},
                        "pubdate": 1_787_301_884,
                        "duration": 691,
                        "pages": pages,
                    },
                },
            )
        if request.url.path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": -101,
                    "data": {
                        "wbi_img": {
                            "img_url": (
                                "https://i0.hdslb.com/bfs/wbi/"
                                "0123456789abcdef0123456789abcdef.png"
                            ),
                            "sub_url": (
                                "https://i0.hdslb.com/bfs/wbi/"
                                "fedcba9876543210fedcba9876543210.png"
                            ),
                        }
                    },
                },
            )
        if request.url.path == "/x/player/wbi/playurl":
            assert request.url.params["bvid"] == bvid
            assert request.url.params["cid"] in {"1001", "1002"}
            assert request.url.params["fnval"] == "4048"
            assert request.url.params["try_look"] == "1"
            assert request.url.params["wts"].isdigit()
            assert len(request.url.params["w_rid"]) == 32
            assert request.url.params["dm_img_list"] == "[]"
            assert json.loads(request.url.params["dm_img_inter"])["ds"] == []
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "dash": {
                            "audio": [
                                {
                                    "id": 30216,
                                    "bandwidth": 64_000,
                                    "mimeType": "audio/mp4",
                                    "baseUrl": "https://audio.example/low.m4s",
                                    "backupUrl": [],
                                },
                                {
                                    "id": 30232,
                                    "bandwidth": 92_000,
                                    "mime_type": "audio/mp4",
                                    "base_url": "https://audio.example/best.m4s",
                                    "backup_url": ["https://backup.example/best.m4s"],
                                },
                            ]
                        }
                    },
                },
            )
        return httpx.Response(404)

    # MockTransport 保留真实查询参数和字段解析。
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


# 标准视频地址应解析为第一页及最高带宽音频。
def test_resolver_selects_first_page_and_best_dash_audio() -> None:
    """验证元数据、规范地址和 DASH 音频候选排序。"""

    # 测试视频 BV 号。
    bvid = "BV19v8x6uEh8"
    # 使用真实字段映射的解析器。
    resolver = BilibiliWebResolver(
        BilibiliSettings(),
        _resolver_client(bvid, multi_page=False),
    )

    # 解析后的作品。
    work = resolver.resolve(f"https://www.bilibili.com/video/{bvid}/")

    assert work.bvid == bvid
    assert work.cid == "1001"
    assert work.page_number == 1
    assert work.title == "测试合集"
    assert work.canonical_url == f"https://www.bilibili.com/video/{bvid}"
    assert work.audio_urls == (
        "https://audio.example/best.m4s",
        "https://backup.example/best.m4s",
    )
    assert work.audio_mime_type == "audio/mp4"


# 多P视频缺少明确序号时不能静默只归档第一P。
def test_resolver_requires_page_number_for_multi_page_video() -> None:
    """验证多P视频的完整性保护。"""

    # 测试视频 BV 号。
    bvid = "BV19v8x6uEh8"
    # 返回多P详情的解析器。
    resolver = BilibiliWebResolver(BilibiliSettings(), _resolver_client(bvid))

    with pytest.raises(ValueError, match="多P视频必须通过 p 参数"):
        resolver.resolve(f"https://www.bilibili.com/video/{bvid}/")


# 分享短链中的分P参数必须在安全跳转后保留。
def test_resolver_follows_b23_short_link_and_selects_requested_page() -> None:
    """验证 b23.tv 短链和分P选择。"""

    # 测试视频 BV 号。
    bvid = "BV19v8x6uEh8"
    # 使用模拟短链的解析器。
    resolver = BilibiliWebResolver(BilibiliSettings(), _resolver_client(bvid))

    # 解析后的第二分P作品。
    work = resolver.resolve("https://b23.tv/test")

    assert work.cid == "1002"
    assert work.page_number == 2
    assert work.title == "测试合集 - 第二集"
    assert work.canonical_url == f"https://www.bilibili.com/video/{bvid}?p=2"
    assert work.platform_content_id == f"{bvid}:p2"


# 非官方主机不得进入平台接口调用。
def test_resolver_rejects_lookalike_host() -> None:
    """验证精确主机白名单。"""

    # 无网络解析器。
    resolver = BilibiliWebResolver(BilibiliSettings(), _resolver_client("BV19v8x6uEh8"))

    with pytest.raises(ValueError, match="官方 HTTPS 主机"):
        resolver.resolve("https://www.bilibili.com.evil.example/video/BV19v8x6uEh8")


# CDN 首选节点失败时应尝试平台返回的备用节点。
def test_media_downloader_falls_back_to_backup_url(tmp_path: Path) -> None:
    """验证音频候选地址回退与流式写入。"""

    # 记录实际访问顺序。
    requested_hosts: list[str] = []

    # 模拟首选节点失败、备用节点成功。
    def handler(request: httpx.Request) -> httpx.Response:
        """返回确定性 CDN 响应。"""

        # 当前请求主机。
        requested_host = request.url.host or ""
        requested_hosts.append(requested_host)
        if requested_host == "primary.example":
            return httpx.Response(403, text="expired")
        return httpx.Response(200, content=b"audio-bytes", headers={"Content-Type": "video/mp4"})

    # 使用内存传输的下载器。
    downloader = BilibiliMediaDownloader(
        BilibiliSettings(),
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    # 临时音频文件。
    destination = tmp_path / "source-audio.m4s"

    # 实际响应类型。
    content_type = downloader.download(
        ("https://primary.example/audio", "https://backup.example/audio"),
        destination,
        max_bytes=1024,
    )

    assert requested_hosts == ["primary.example", "backup.example"]
    assert destination.read_bytes() == b"audio-bytes"
    assert content_type == "video/mp4"


# 记录 COS 临时对象生命周期的 Fake。
class FakeArtifactStore:
    """保存上传和删除记录。"""

    # 初始化内存状态。
    def __init__(self) -> None:
        """初始化 Fake。"""

        # 上传对象引用。
        self.put_refs: list[str] = []
        # 删除对象引用。
        self.deleted_refs: list[str] = []

    # 保存音频对象并返回不透明引用。
    def put(self, data: bytes, suffix: str) -> str:
        """模拟上传。"""

        assert data
        assert suffix == ".mp3"
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
    def __init__(self, work: BilibiliWork) -> None:
        """初始化 Fake。"""

        self._work = work
        # 解析调用次数。
        self.calls = 0

    # 忽略地址返回预置作品。
    def resolve(self, url: str) -> BilibiliWork:
        """返回固定作品。"""

        self.calls += 1
        return self._work


# 写入假音频内容。
class FakeDownloader:
    """避免访问 B站 CDN。"""

    # 写入确定性音频字节。
    def download(self, urls: tuple[str, ...], destination: Path, max_bytes: int) -> str:
        """模拟音频下载。"""

        assert urls
        assert max_bytes > 0
        destination.write_bytes(b"source-audio")
        return "audio/mp4"


# 第一次模拟短期媒体地址全部失效，刷新后恢复。
class RefreshingDownloader(FakeDownloader):
    """验证播放地址只刷新一次。"""

    # 初始化下载次数。
    def __init__(self) -> None:
        """初始化 Fake。"""

        # 下载调用次数。
        self.calls = 0

    # 第一次失败，第二次写入音频。
    def download(self, urls: tuple[str, ...], destination: Path, max_bytes: int) -> str:
        """模拟短期地址失效和刷新成功。"""

        self.calls += 1
        if self.calls == 1:
            raise BilibiliMediaUnavailableError("expired")
        return super().download(urls, destination, max_bytes)


# 写入假标准音频。
class FakeAudioExtractor:
    """避免调用 FFmpeg。"""

    # 生成两个最小音频分段。
    def extract_segments(
        self,
        video_path: Path,
        output_directory: Path,
    ) -> tuple[Path, ...]:
        """模拟音频标准化和分段。"""

        assert video_path.read_bytes() == b"source-audio"
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

    # 保存可配置转录文本。
    def __init__(self, text: str = "这是完整视频转录。") -> None:
        """初始化 Fake。"""

        # 每个分段返回的文本。
        self._text = text

    # 验证输入是本地标准音频。
    def transcribe(self, audio_path: Path) -> AsrTranscription:
        """模拟音频转录。"""

        assert audio_path.read_bytes() == b"audio"
        return AsrTranscription(text=self._text)


# B站视频应复用音频分段、ASR 和临时对象清理流程。
def test_content_extractor_transcribes_audio_and_deletes_artifacts(tmp_path: Path) -> None:
    """验证 B站视频到规范内容的完整适配器生命周期。"""

    # 预置B站作品。
    work = BilibiliWork(
        source_url="https://www.bilibili.com/video/BV19v8x6uEh8/",
        canonical_url="https://www.bilibili.com/video/BV19v8x6uEh8?p=1",
        bvid="BV19v8x6uEh8",
        cid="1001",
        page_number=1,
        platform_content_id="BV19v8x6uEh8",
        title="测试视频",
        author="测试作者",
        description="视频简介",
        published_at=None,
        duration_seconds=691,
        audio_urls=("https://audio.example/source.m4s",),
        audio_mime_type="audio/mp4",
    )
    # 进度阶段列表。
    stages: list[str] = []
    # 受控本地临时目录。
    workspace_root = tmp_path / "temp"
    # 被测提取器。
    extractor = BilibiliContentExtractor(
        settings=BilibiliSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        transcriber=AudioTranscriptionService(FakeAsr()),
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
        temp_workspaces=LocalTempWorkspaceFactory(
            TempStorageSettings(local_root=workspace_root)
        ),
    )

    # 规范内容结果。
    content = extractor.extract(work.source_url, lambda stage, _data: stages.append(stage))

    assert content.platform == "B站"
    assert content.content_type is ContentType.VIDEO
    assert content.quality is ContentQuality.FULL
    assert content.platform_content_id == work.platform_content_id
    assert "视频简介" in content.body_text
    assert content.body_text.count("完整视频转录") == 2
    assert list(workspace_root.iterdir()) == []
    assert stages == [
        "bilibili_resolved",
        "bilibili_audio_downloaded",
        "bilibili_audio_extracted",
        "bilibili_asr_segment_completed",
        "bilibili_asr_segment_completed",
        "bilibili_asr_completed",
    ]


# 所有 CDN 候选失效时应重新解析一次，不能无限刷新。
def test_content_extractor_refreshes_expired_audio_urls_once(tmp_path: Path) -> None:
    """验证短期播放地址的一次性刷新。"""

    # 预置单P作品。
    work = BilibiliWork(
        source_url="https://www.bilibili.com/video/BV19v8x6uEh8/",
        canonical_url="https://www.bilibili.com/video/BV19v8x6uEh8",
        bvid="BV19v8x6uEh8",
        cid="1001",
        page_number=1,
        platform_content_id="BV19v8x6uEh8",
        title="测试视频",
        author="测试作者",
        description="视频简介",
        published_at=None,
        duration_seconds=691,
        audio_urls=("https://audio.example/expired.m4s",),
        audio_mime_type="audio/mp4",
    )
    # 记录解析次数的 Fake。
    resolver = FakeResolver(work)
    # 首次失败后成功的下载器。
    downloader = RefreshingDownloader()
    # 进度阶段列表。
    stages: list[str] = []
    # 被测提取器。
    extractor = BilibiliContentExtractor(
        settings=BilibiliSettings(),
        resolver=resolver,  # type: ignore[arg-type]
        downloader=downloader,  # type: ignore[arg-type]
        transcriber=AudioTranscriptionService(FakeAsr()),
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
        temp_workspaces=LocalTempWorkspaceFactory(
            TempStorageSettings(local_root=tmp_path / "temp")
        ),
    )

    extractor.extract(work.source_url, lambda stage, _data: stages.append(stage))

    assert resolver.calls == 2
    assert downloader.calls == 2
    assert stages.count("bilibili_audio_urls_refreshed") == 1


# 空 ASR 结果必须失败，不能留下伪完成检查点。
def test_content_extractor_rejects_empty_transcript(tmp_path: Path) -> None:
    """验证完整视频摘要只建立在真实转录证据上。"""

    # 预置单P作品。
    work = BilibiliWork(
        source_url="https://www.bilibili.com/video/BV19v8x6uEh8/",
        canonical_url="https://www.bilibili.com/video/BV19v8x6uEh8",
        bvid="BV19v8x6uEh8",
        cid="1001",
        page_number=1,
        platform_content_id="BV19v8x6uEh8",
        title="测试视频",
        author="测试作者",
        description="视频简介",
        published_at=None,
        duration_seconds=691,
        audio_urls=("https://audio.example/source.m4s",),
        audio_mime_type="audio/mp4",
    )
    # 进度阶段列表。
    stages: list[str] = []
    # 受控本地临时目录。
    workspace_root = tmp_path / "temp"
    # 返回空文本的被测提取器。
    extractor = BilibiliContentExtractor(
        settings=BilibiliSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        transcriber=AudioTranscriptionService(FakeAsr("")),
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
        temp_workspaces=LocalTempWorkspaceFactory(
            TempStorageSettings(local_root=workspace_root)
        ),
    )

    with pytest.raises(ValueError, match="转录为空"):
        extractor.extract(work.source_url, lambda stage, _data: stages.append(stage))

    assert list(workspace_root.iterdir()) == []
    assert "bilibili_asr_completed" not in stages
