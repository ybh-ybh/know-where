"""小红书公开页面解析和多媒体分支测试。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from knowwhere.adapters.local_temp_storage import LocalTempWorkspaceFactory
from knowwhere.adapters.xiaohongshu import (
    XiaohongshuContentExtractor,
    XiaohongshuMediaDownloader,
    XiaohongshuWebResolver,
    XiaohongshuWork,
)
from knowwhere.application.media import AudioTranscriptionService
from knowwhere.application.ports import AsrTranscription, VisionInput
from knowwhere.config import TempStorageSettings, XiaohongshuSettings
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


# 最小详情页用于验证请求链路，不依赖下载、模型和外部归档。
def _session_detail_html(note_id: str) -> str:
    """构造带一张图片的合法笔记。"""

    return _detail_html(
        note_id,
        {
            "noteId": note_id,
            "type": "normal",
            "title": "会话测试",
            "desc": "完整正文",
            "imageList": [{"urlDefault": "https://sns-webpic-qc.xhscdn.com/image"}],
        },
    )


# 客户端默认 Cookie、Cookie jar 和重定向设置不得突破解析器发送边界。
@pytest.mark.parametrize("cookie", [None, SecretStr("a1=configured-secret")])
def test_resolver_cookie_is_scoped_to_detail_hosts(cookie: SecretStr | None) -> None:
    """验证短链往返和两个详情主机的凭据隔离。"""

    # 固定目标身份。
    note_id = "6a8c08720000000028031d9b"
    # 每一跳期望地址，覆盖详情页再跳回短链的情况。
    urls = [
        "https://xhslink.cn/o/start",
        f"https://www.xiaohongshu.com/discovery/item/{note_id}?xsec_token=test=",
        "https://xhslink.com/o/next",
        f"https://xiaohongshu.com/explore/{note_id}?xsec_token=test=&xsec_source=pc_share",
    ]
    # 收到的请求用于证明 Cookie 没有跨主机继承。
    requests: list[httpx.Request] = []

    # 按指定链路模拟分享跳转和有效页面。
    def handler(request: httpx.Request) -> httpx.Response:
        """同时模拟服务端尝试通过 Set-Cookie 建立广域会话。"""

        requests.append(request)
        assert str(request.url) == urls[len(requests) - 1]
        if len(requests) < len(urls):
            return httpx.Response(
                302,
                headers={
                    "Location": urls[len(requests)],
                    "Set-Cookie": "server-secret=hidden; Path=/",
                },
            )
        return httpx.Response(200, text=_session_detail_html(note_id))

    # 注入与生产默认不同的客户端，验证解析器仍强制安全行为。
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        headers={"Cookie": "default-secret=hidden"},
        cookies={"jar-secret": "hidden"},
    )
    # 显式配置的会话是详情请求唯一的 Cookie 来源。
    resolver = XiaohongshuWebResolver(XiaohongshuSettings(cookie=cookie), client)
    # 解析仍复用已有字段和媒体转换逻辑。
    work = resolver.resolve(urls[0])
    assert work.note_id == note_id
    assert work.canonical_url == f"https://www.xiaohongshu.com/explore/{note_id}"
    assert work.source_url == urls[0]
    for request in requests:
        assert request.headers.get("Cookie") == (
            cookie.get_secret_value()
            if cookie and request.url.host in {"www.xiaohongshu.com", "xiaohongshu.com"}
            else None
        )


# 明确的错误页应在下一跳网络请求前识别，其他非笔记路径不能冒充详情。
@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("/login?redirectPath=private-token", "引导至登录页"),
        ("/login/?token=private-token", "引导至登录页"),
        ("/404/sec_test?token=private-token", "安全页面拦截"),
        ("/404?token=private-token", "不可访问"),
        ("/explore?token=private-token", "非笔记页面"),
        ("/", "非笔记页面"),
        ("https://example.com/?token=private-token", "官方 HTTPS 主机"),
        ("https://www.xiaohongshu.com:private-token/", "响应或跳转地址格式无效"),
        ("/explore/000000000000000000000001", "不同的笔记"),
    ],
)
@pytest.mark.parametrize("cookie", [None, SecretStr("a1=private-cookie")])
def test_resolver_classifies_redirect_before_request(
    target: str, message: str, cookie: SecretStr | None
) -> None:
    """确保错误回执不带原始 URL、Cookie 或令牌。"""

    # 只允许请求原始详情一次。
    requests: list[httpx.Request] = []

    # 返回指定异常跳转。
    def handler(request: httpx.Request) -> httpx.Response:
        """若解析器错误跟随跳转，立即让测试失败。"""

        requests.append(request)
        assert len(requests) == 1
        return httpx.Response(302, headers={"Location": target})

    # 使用自动跟随客户端检验逐跳控制确实生效。
    resolver = XiaohongshuWebResolver(
        XiaohongshuSettings(cookie=cookie),
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    with pytest.raises(ValueError, match=message) as caught:
        resolver.resolve(
            "https://www.xiaohongshu.com/discovery/item/6a8c08720000000028031d9b"
            "?xsec_token=private-token"
        )
    assert len(requests) == 1
    assert "private-token" not in str(caught.value)
    assert "private-cookie" not in str(caught.value)
    if "login" in target:
        assert "KW_XIAOHONGSHU_COOKIE" in str(caught.value)
        assert ("检查或更新" in str(caught.value)) == (cookie is not None)


# HTTP 错误和网络异常不得再透传 HTTPX 附带的完整访问 URL。
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (401, "访问受限"),
        (403, "访问受限"),
        (461, "访问受限"),
        (471, "访问受限"),
        (429, "限流"),
        (404, "不存在或不可访问"),
        (500, "HTTP 500"),
        ("timeout", "超时"),
        ("connection", "网络请求失败"),
    ],
)
def test_resolver_sanitizes_request_failures(failure: int | str, message: str) -> None:
    """验证错误分类和脱敏。"""

    # 在请求发生后模拟状态码或包含敏感文本的底层异常。
    def handler(request: httpx.Request) -> httpx.Response:
        """生产回执不能包含底层异常的原始文本。"""

        if failure == "timeout":
            raise httpx.ReadTimeout("private-token private-cookie", request=request)
        if failure == "connection":
            raise httpx.ConnectError("private-token private-cookie", request=request)
        assert isinstance(failure, int)
        return httpx.Response(failure, text="private-token private-cookie")

    # 具有有效格式的会话配置。
    resolver = XiaohongshuWebResolver(
        XiaohongshuSettings(cookie=SecretStr("a1=private-cookie")),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ValueError, match=message) as caught:
        resolver.resolve(
            "https://www.xiaohongshu.com/explore/6a8c08720000000028031d9b?xsec_token=private-token"
        )
    assert "private-token" not in str(caught.value)
    assert "private-cookie" not in str(caught.value)


# 200 状态不能替代页面结构和目标身份验证。
@pytest.mark.parametrize("body_kind", ["empty", "wrong_map", "wrong_note"])
def test_resolver_rejects_missing_or_mismatched_detail(body_kind: str) -> None:
    """页面缺失和笔记不匹配必须终止解析。"""

    # 当前请求的笔记身份。
    note_id = "6a8c08720000000028031d9b"
    # 另一篇笔记，用于模拟返回了错误内容。
    other_id = "000000000000000000000001"
    # 默认返回登录提示内容，但路径仍是详情地址。
    html = "<html>请登录</html>"
    if body_kind == "wrong_map":
        html = _session_detail_html(other_id)
    elif body_kind == "wrong_note":
        html = _detail_html(note_id, {"noteId": other_id})
    # 固定响应客户端。
    resolver = XiaohongshuWebResolver(
        XiaohongshuSettings(),
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html))),
    )
    with pytest.raises(ValueError, match="没有公开笔记数据|未返回目标笔记|不匹配的笔记"):
        resolver.resolve(f"https://www.xiaohongshu.com/explore/{note_id}")


# 页面会话配置不能被独立媒体下载器带到 CDN 请求中。
def test_media_downloader_does_not_send_configured_cookie(tmp_path: Path) -> None:
    """配置 Cookie 后仍按原有无账号凭据的方式下载媒体。"""

    # 记录实际下载请求。
    requests: list[httpx.Request] = []

    # 模拟媒体 CDN 响应。
    def handler(request: httpx.Request) -> httpx.Response:
        """下载请求不能带页面 Cookie。"""

        requests.append(request)
        assert "Cookie" not in request.headers
        return httpx.Response(200, content=b"image", headers={"Content-Type": "image/jpeg"})

    # 下载器与解析器接收同一个配置模型，但不使用其中的 Cookie。
    downloader = XiaohongshuMediaDownloader(
        XiaohongshuSettings(cookie=SecretStr("a1=private-cookie")),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert (
        downloader.download(
            ("https://sns-webpic-qc.xhscdn.com/image",), tmp_path / "image.jpg", 1024
        )
        == "image/jpeg"
    )
    assert len(requests) == 1


# 不合法的原始详情链接必须在联网前失败。
def test_resolver_rejects_invalid_id_before_network() -> None:
    """提前解析不能接受非 24 位 ID 或多余的路径前缀。"""

    # 任何网络调用都说明输入验证过晚。
    def handler(_request: httpx.Request) -> httpx.Response:
        """非法输入不应发起网络请求。"""

        pytest.fail("非法笔记路径触发了网络请求")

    # 注入拒绝所有请求的客户端。
    resolver = XiaohongshuWebResolver(
        XiaohongshuSettings(), httpx.Client(transport=httpx.MockTransport(handler))
    )
    for path in ("/explore/invalid", "/extra/explore/6a8c08720000000028031d9b"):
        with pytest.raises(ValueError, match="没有可识别的笔记 ID"):
            resolver.resolve("https://www.xiaohongshu.com" + path)


# 分享短链返回 200 或形成循环时不能被误认为已经拿到详情。
@pytest.mark.parametrize("redirect_loop", [False, True])
def test_resolver_requires_resolved_short_link(redirect_loop: bool) -> None:
    """保持详情主机边界和有限跳转约束。"""

    # 实际网络请求次数。
    requests: list[httpx.Request] = []

    # 返回循环跳转或伪装成详情页的短链响应。
    def handler(request: httpx.Request) -> httpx.Response:
        """短链主机上的详情路径不能作为最终页面。"""

        requests.append(request)
        if redirect_loop:
            return httpx.Response(302, headers={"Location": str(request.url)})
        return httpx.Response(200, text=_session_detail_html("6a8c08720000000028031d9b"))

    # 真实跳转控制逻辑。
    resolver = XiaohongshuWebResolver(
        XiaohongshuSettings(), httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError, match="跳转次数过多|短链未返回笔记详情"):
        resolver.resolve("https://xhslink.cn/explore/6a8c08720000000028031d9b")
    assert len(requests) == (6 if redirect_loop else 1)


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

        # 最近图片文件名。
        self.image_names: tuple[str, ...] = ()

    # 返回固定图文正文。
    def describe(self, images: tuple[VisionInput, ...], caption: str) -> str:
        """模拟多图理解。"""

        assert all(image.path.read_bytes() == b"media" for image in images)
        # 保留可在工作区清理后断言的文件名。
        self.image_names = tuple(image.path.name for image in images)
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
    # 视觉 Fake。
    vision = FakeVision()
    # 受控本地临时目录。
    workspace_root = tmp_path / "temp"
    # 被测提取器。
    extractor = XiaohongshuContentExtractor(
        settings=XiaohongshuSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),  # type: ignore[arg-type]
        transcriber=AudioTranscriptionService(FakeAsr()),
        vision=vision,
        audio_extractor=FakeAudioExtractor(),  # type: ignore[arg-type]
        temp_workspaces=LocalTempWorkspaceFactory(
            TempStorageSettings(local_root=workspace_root)
        ),
    )

    # 领域提取结果。
    content = extractor.extract(work.source_url)

    assert content.platform == "小红书"
    assert content.content_type is ContentType.IMAGE_TEXT
    assert "完整 OCR" in content.body_text
    assert vision.image_names == ("image-0.bin", "image-1.bin")
    assert list(workspace_root.iterdir()) == []


# 视频链路必须合并配文与全部分段转录并清理音频对象。
def test_video_extractor_transcribes_and_deletes_audio(tmp_path: Path) -> None:
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
    # 记录本地 ASR 调用。
    asr = FakeAsr()
    # 受控本地临时目录。
    workspace_root = tmp_path / "temp"
    # 被测提取器。
    extractor = XiaohongshuContentExtractor(
        settings=XiaohongshuSettings(),
        resolver=FakeResolver(work),  # type: ignore[arg-type]
        downloader=FakeDownloader(),  # type: ignore[arg-type]
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
