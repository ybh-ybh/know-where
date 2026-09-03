"""小红书公开图文与视频内容提取器。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from knowwhere.adapters.douyin import FfmpegAudioExtractor
from knowwhere.adapters.local_temp_storage import LocalTempWorkspaceFactory
from knowwhere.application.media import AudioTranscriptionService
from knowwhere.application.ports import (
    ContentExtractorPort,
    ExtractionProgress,
    VisionInput,
    VisionProviderPort,
)
from knowwhere.config import XiaohongshuSettings
from knowwhere.domain.models import ContentQuality, ContentType, ExtractedContent

# 小红书页面和媒体请求使用稳定桌面浏览器标识。
XIAOHONGSHU_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
    "Safari/537.36"
)
# 允许用户提交和短链跳转到的精确官方主机名。
XIAOHONGSHU_PAGE_HOSTS = frozenset(
    {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.cn", "xhslink.com"}
)
# 页面状态里的媒体只能来自小红书自有 CDN 域。
XIAOHONGSHU_MEDIA_DOMAIN_SUFFIXES = ("xhscdn.com", "xiaohongshu.com")
# 分享短链最多允许的安全跳转次数。
XIAOHONGSHU_MAX_REDIRECTS = 5
# 页面内嵌状态脚本，只截取当前 script 标签中的 JSON 对象。
XIAOHONGSHU_INITIAL_STATE_PATTERN = re.compile(
    r"<script[^>]*>\s*window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
    re.DOTALL,
)
# 小红书笔记 ID 当前为 24 位十六进制字符串。
XIAOHONGSHU_NOTE_ID_PATTERN = re.compile(r"[0-9a-fA-F]{24}")


# 解析阶段只保留稳定元数据和当前请求可用的短期媒体地址。
@dataclass(frozen=True, slots=True)
class XiaohongshuWork:
    """小红书笔记解析结果。"""

    # 用户提交的原始地址。
    source_url: str
    # 不携带访问令牌的稳定规范地址。
    canonical_url: str
    # 小红书笔记 ID。
    note_id: str
    # 笔记内容类型。
    content_type: ContentType
    # 笔记标题。
    title: str
    # 作者昵称。
    author: str | None
    # 笔记配文。
    caption: str
    # 发布时间。
    published_at: datetime | None
    # 按笔记顺序排列的图片地址。
    image_urls: tuple[str, ...] = ()
    # 同一视频流的首选和备用地址。
    video_urls: tuple[str, ...] = ()
    # 平台声明的视频时长毫秒数。
    duration_milliseconds: int | None = None


# 小红书 Web 解析器负责短链还原、页面状态读取和字段兼容。
class XiaohongshuWebResolver:
    """解析无需登录即可访问的小红书公开笔记。"""

    # 保存请求配置和可替换 HTTP 客户端。
    def __init__(
        self,
        settings: XiaohongshuSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化解析器。"""

        # 小红书媒体处理限制。
        self._settings = settings
        # 页面跳转由解析器逐跳校验，防止短链被用于任意请求。
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
            headers={
                "User-Agent": XIAOHONGSHU_USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )

    # 还原链接并把页面初始状态转换为统一工作对象。
    def resolve(self, url: str) -> XiaohongshuWork:
        """返回小红书图文或视频笔记。"""

        # 去除用户复制链接两端空白但保留原始查询参数。
        source_url = url.strip()
        self._validate_page_url(source_url)
        # 最终详情地址与对应 HTML，避免还原后重复请求页面。
        detail_url, html = self._request_detail_page(source_url)
        # 从详情路径提取的笔记 ID。
        note_id = self._extract_note_id(detail_url)
        # 页面初始状态对象。
        initial_state = self._extract_initial_state(html)
        # 笔记详情映射。
        note_detail_map = self._nested_dict(initial_state, "note", "noteDetailMap")
        # 当前笔记容器。
        note_container = note_detail_map.get(note_id)
        if not isinstance(note_container, dict):
            raise ValueError("小红书页面未返回目标笔记详情，可能需要登录或已触发验证")
        # 当前笔记原始字段。
        note = note_container.get("note")
        if not isinstance(note, dict):
            raise ValueError("小红书页面中的目标笔记详情格式无效")
        return self._to_work(source_url, note_id, note)

    # 逐跳请求页面并限制所有重定向目标。
    def _request_detail_page(self, url: str) -> tuple[str, str]:
        """返回最终详情地址和 HTML。"""

        # 当前待请求地址。
        current_url = url
        for _redirect_index in range(XIAOHONGSHU_MAX_REDIRECTS + 1):
            # 当前页面响应。
            response = self._client.get(current_url)
            if response.is_redirect:
                # 下一跳地址可能是相对路径。
                location = response.headers.get("location")
                if not location:
                    raise ValueError("小红书短链跳转缺少 Location")
                # 解析后的下一跳绝对地址。
                next_url = urljoin(current_url, location)
                self._validate_page_url(next_url)
                current_url = next_url
                continue
            response.raise_for_status()
            self._validate_detail_path(current_url)
            return current_url, response.text
        raise ValueError("小红书短链跳转次数过多")

    # 从内嵌脚本读取 JSON，兼容服务端状态里的 JavaScript undefined。
    @staticmethod
    def _extract_initial_state(html: str) -> dict[str, Any]:
        """返回页面初始状态对象。"""

        if "noteDetailMap" not in html:
            raise ValueError("小红书页面没有公开笔记数据，可能需要登录或已触发验证")
        # 初始状态脚本匹配。
        match = XIAOHONGSHU_INITIAL_STATE_PATTERN.search(html)
        if match is None:
            raise ValueError("小红书页面初始状态格式已变化")
        # 只把 JSON 值位置的 undefined 转成 null，避免修改正文字符串。
        state_text = re.sub(
            r"(?<=[,:\[])\s*undefined\s*(?=[,}\]])",
            "null",
            match.group(1),
        ).replace("new Map([])", "[]")
        try:
            # 已解码初始状态。
            state = json.loads(state_text)
        except json.JSONDecodeError as error:
            raise ValueError("小红书页面初始状态不是有效 JSON") from error
        if not isinstance(state, dict):
            raise ValueError("小红书页面初始状态不是对象")
        return state

    # 把平台字段转换成稳定领域字段。
    def _to_work(
        self,
        source_url: str,
        note_id: str,
        note: dict[str, Any],
    ) -> XiaohongshuWork:
        """构建统一小红书工作对象。"""

        # 平台返回的真实笔记 ID。
        response_note_id = str(note.get("noteId") or "").strip()
        if response_note_id != note_id:
            raise ValueError("小红书页面返回了不匹配的笔记 ID")
        # 平台笔记类型。
        note_type = str(note.get("type") or "").strip().lower()
        # 去除首尾空白的笔记配文。
        caption = str(note.get("desc") or "").strip()
        # 平台标题，空标题时使用配文首行。
        title = str(note.get("title") or "").strip()
        if not title and caption:
            title = caption.splitlines()[0].strip()
        if not title:
            title = f"小红书笔记 {note_id}"
        # 作者对象。
        user = note.get("user")
        # 作者昵称。
        author = (
            str(user.get("nickname") or user.get("nickName") or "").strip()
            if isinstance(user, dict)
            else ""
        )
        # 毫秒发布时间。
        published_milliseconds = self._safe_int(note.get("time"))
        # 带时区的发布时间。
        published_at = (
            datetime.fromtimestamp(published_milliseconds / 1000, tz=UTC)
            if published_milliseconds is not None
            else None
        )
        if note_type == "video":
            # 视频首选和备用地址。
            video_urls, duration_milliseconds = self._extract_video_urls(note)
            if not video_urls:
                raise ValueError("小红书视频笔记缺少可用播放地址")
            return XiaohongshuWork(
                source_url=source_url,
                canonical_url=f"https://www.xiaohongshu.com/explore/{note_id}",
                note_id=note_id,
                content_type=ContentType.VIDEO,
                title=title,
                author=author or None,
                caption=caption,
                published_at=published_at,
                video_urls=video_urls,
                duration_milliseconds=duration_milliseconds,
            )
        # 有序图文图片地址。
        image_urls = self._extract_image_urls(note)
        if not image_urls:
            raise ValueError("小红书图文笔记缺少可用图片")
        return XiaohongshuWork(
            source_url=source_url,
            canonical_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            note_id=note_id,
            content_type=ContentType.IMAGE_TEXT,
            title=title,
            author=author or None,
            caption=caption,
            published_at=published_at,
            image_urls=image_urls,
        )

    # 按页面顺序提取每张图片的默认清晰度地址。
    def _extract_image_urls(self, note: dict[str, Any]) -> tuple[str, ...]:
        """返回经过 CDN 白名单校验的有序图片地址。"""

        # 原始图片列表。
        image_list = note.get("imageList")
        if not isinstance(image_list, list):
            return ()
        if len(image_list) > self._settings.max_images:
            raise ValueError("小红书图文图片数量超过配置上限")
        # 最终有序图片地址。
        image_urls: list[str] = []
        for image in image_list:
            if not isinstance(image, dict):
                raise ValueError("小红书图文包含无效图片字段")
            # 优先使用页面给出的默认清晰度地址。
            raw_url = str(image.get("urlDefault") or "").strip()
            if not raw_url:
                # 图片场景列表中的默认地址。
                info_list = image.get("infoList")
                if isinstance(info_list, list):
                    for info in info_list:
                        if isinstance(info, dict) and info.get("imageScene") == "WB_DFT":
                            raw_url = str(info.get("url") or "").strip()
                            break
            if not raw_url:
                raw_url = str(image.get("urlPre") or image.get("url") or "").strip()
            if not raw_url:
                raise ValueError("小红书图文包含缺少地址的图片")
            image_urls.append(self._normalize_media_url(raw_url))
        return tuple(image_urls)

    # 从所有编码流中选择最高分辨率视频并保留同流备用 CDN。
    def _extract_video_urls(
        self,
        note: dict[str, Any],
    ) -> tuple[tuple[str, ...], int | None]:
        """返回视频地址候选和时长。"""

        # 视频媒体对象。
        media = self._nested_dict(note, "video", "media")
        # 编码类型到流列表的映射。
        stream_map = media.get("stream")
        if not isinstance(stream_map, dict):
            return (), None
        # 所有合法视频流。
        streams: list[dict[str, Any]] = []
        for stream_items in stream_map.values():
            if isinstance(stream_items, list):
                streams.extend(item for item in stream_items if isinstance(item, dict))
        if not streams:
            return (), None
        # 分辨率、码率和文件大小共同决定首选视频流。
        selected_stream = max(
            streams,
            key=lambda item: (
                (self._safe_int(item.get("width")) or 0)
                * (self._safe_int(item.get("height")) or 0),
                self._safe_int(item.get("videoBitrate")) or 0,
                self._safe_int(item.get("size")) or 0,
            ),
        )
        # 首选地址后跟平台提供的备用地址。
        raw_urls = [selected_stream.get("masterUrl")]
        # 备用地址列表。
        backup_urls = selected_stream.get("backupUrls")
        if isinstance(backup_urls, list):
            raw_urls.extend(backup_urls)
        # 去重后的安全候选地址。
        video_urls: list[str] = []
        for raw_url in raw_urls:
            if not isinstance(raw_url, str) or not raw_url.strip():
                continue
            # 规范化后的媒体地址。
            normalized_url = self._normalize_media_url(raw_url)
            if normalized_url not in video_urls:
                video_urls.append(normalized_url)
        # 当前流的毫秒时长。
        duration_milliseconds = self._safe_int(
            selected_stream.get("videoDuration") or selected_stream.get("duration")
        )
        return tuple(video_urls), duration_milliseconds

    # 只接受小红书官方 HTTPS 页面地址。
    @staticmethod
    def _validate_page_url(url: str) -> None:
        """校验页面 URL 主机、协议和用户信息。"""

        # 解析后的页面地址。
        parts = urlsplit(url)
        # 小写页面主机名。
        hostname = (parts.hostname or "").lower()
        if parts.scheme != "https" or hostname not in XIAOHONGSHU_PAGE_HOSTS:
            raise ValueError("小红书链接必须使用受支持的官方 HTTPS 主机")
        if parts.username or parts.password or parts.port is not None:
            raise ValueError("小红书链接不能包含用户信息或自定义端口")

    # 最终页面必须是单篇笔记路径。
    @classmethod
    def _validate_detail_path(cls, url: str) -> None:
        """拒绝跳转到首页、登录页或其他非笔记页面。"""

        cls._extract_note_id(url)

    # 从 explore 或 discovery/item 路径提取笔记 ID。
    @staticmethod
    def _extract_note_id(url: str) -> str:
        """返回详情地址中的小红书笔记 ID。"""

        # 不含空段的路径片段。
        segments = tuple(segment for segment in urlsplit(url).path.split("/") if segment)
        # 支持的详情路径前缀。
        candidate: str | None = None
        if len(segments) >= 2 and segments[-2] == "explore":
            candidate = segments[-1]
        elif len(segments) >= 3 and segments[-3:-1] == ("discovery", "item"):
            candidate = segments[-1]
        if candidate and XIAOHONGSHU_NOTE_ID_PATTERN.fullmatch(candidate):
            return candidate.lower()
        raise ValueError("小红书链接中没有可识别的笔记 ID")

    # 把页面内 HTTP CDN 地址升级为 HTTPS 并执行域名白名单校验。
    @staticmethod
    def _normalize_media_url(url: str) -> str:
        """返回安全的小红书媒体地址。"""

        # 解析后的媒体地址。
        parts = urlsplit(url.strip())
        # 小写媒体主机名。
        hostname = (parts.hostname or "").lower()
        # CDN 域名必须等于或从属于允许后缀。
        allowed_host = any(
            hostname == suffix or hostname.endswith(f".{suffix}")
            for suffix in XIAOHONGSHU_MEDIA_DOMAIN_SUFFIXES
        )
        if parts.scheme not in {"http", "https"} or not allowed_host:
            raise ValueError("小红书媒体地址不是受支持的官方 CDN")
        if parts.username or parts.password or parts.port is not None:
            raise ValueError("小红书媒体地址不能包含用户信息或自定义端口")
        return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))

    # 读取任意深度的字典字段，遇到类型变化时返回空对象。
    @staticmethod
    def _nested_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
        """返回嵌套字典或空对象。"""

        # 当前嵌套值。
        current: object = data
        for key in keys:
            if not isinstance(current, dict):
                return {}
            current = current.get(key)
        return current if isinstance(current, dict) else {}

    # 把平台数字字段安全转换为整数。
    @staticmethod
    def _safe_int(value: object) -> int | None:
        """返回整数或 None。"""

        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


# 小红书媒体下载器执行大小限制和 CDN 回退。
class XiaohongshuMediaDownloader:
    """把短时 CDN 媒体流式写入本地临时文件。"""

    # 保存请求配置和可替换 HTTP 客户端。
    def __init__(
        self,
        settings: XiaohongshuSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化媒体下载器。"""

        # 下载限制配置。
        self._settings = settings
        # 媒体 CDN 可以在自己的 HTTPS 地址间跳转。
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=True,
            headers={
                "User-Agent": XIAOHONGSHU_USER_AGENT,
                "Referer": "https://www.xiaohongshu.com/",
            },
        )

    # 按顺序尝试首选和备用 CDN 地址。
    def download(
        self,
        urls: tuple[str, ...],
        destination: Path,
        max_bytes: int,
    ) -> str:
        """下载首个可用媒体并返回 Content-Type。"""

        if not urls:
            raise ValueError("小红书媒体候选地址为空")
        # 最后一次下载异常，用于保留因果链。
        last_error: Exception | None = None
        for url in urls:
            try:
                return self._download_one(url, destination, max_bytes)
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                destination.unlink(missing_ok=True)
        raise RuntimeError("小红书媒体候选地址均下载失败") from last_error

    # 流式下载单个媒体并在超限前中止。
    def _download_one(self, url: str, destination: Path, max_bytes: int) -> str:
        """返回单个媒体响应的 Content-Type。"""

        if not url.startswith("https://"):
            raise ValueError("小红书媒体地址必须使用 HTTPS")
        # 已写入字节数。
        written_bytes = 0
        with self._client.stream("GET", url) as response:
            response.raise_for_status()
            # 服务器声明的内容长度。
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("小红书媒体文件超过配置大小限制")
            with destination.open("wb") as output_file:
                for chunk in response.iter_bytes():
                    written_bytes += len(chunk)
                    if written_bytes > max_bytes:
                        raise ValueError("小红书媒体文件超过配置大小限制")
                    output_file.write(chunk)
            # 媒体响应类型。
            content_type = str(response.headers.get("content-type", ""))
        if written_bytes == 0:
            raise ValueError("小红书媒体下载结果为空")
        return content_type.split(";", 1)[0].strip().lower()


# 小红书内容提取器复用视觉、本地音频标准化与 ASR 端口。
class XiaohongshuContentExtractor(ContentExtractorPort):
    """把小红书图文与视频转换为 ExtractedContent。"""

    # 注入平台解析和媒体理解所需副作用端口。
    def __init__(
        self,
        settings: XiaohongshuSettings,
        resolver: XiaohongshuWebResolver,
        downloader: XiaohongshuMediaDownloader,
        transcriber: AudioTranscriptionService,
        vision: VisionProviderPort | None,
        audio_extractor: FfmpegAudioExtractor,
        temp_workspaces: LocalTempWorkspaceFactory,
    ) -> None:
        """初始化小红书提取器。"""

        # 小红书媒体限制。
        self._settings = settings
        # 公开笔记解析器。
        self._resolver = resolver
        # CDN 媒体下载器。
        self._downloader = downloader
        # 供应商无关的分段转录服务。
        self._transcriber = transcriber
        # 可选视觉模型。
        self._vision = vision
        # 本地音频标准化器。
        self._audio_extractor = audio_extractor
        # 可配置的本地临时工作区。
        self._temp_workspaces = temp_workspaces

    # 按真实笔记类型执行图文或视频分支。
    def extract(
        self,
        url: str,
        progress: ExtractionProgress | None = None,
    ) -> ExtractedContent:
        """提取小红书图文或视频正文。"""

        # 解析后的笔记工作对象。
        work = self._resolver.resolve(url)
        self._report(
            progress,
            "xiaohongshu_resolved",
            {"note_id": work.note_id, "content_type": work.content_type.value},
        )
        if work.content_type is ContentType.IMAGE_TEXT:
            # 图文视觉正文与清理警告。
            body_text, warnings = self._extract_image_text(work, progress)
        else:
            # 视频配文、语音转录与清理警告。
            body_text, warnings = self._extract_video(work, progress)
        return ExtractedContent(
            source_url=work.source_url,
            canonical_url=work.canonical_url,
            platform="小红书",
            title=work.title,
            author=work.author,
            body_text=body_text,
            published_at=work.published_at,
            quality=ContentQuality.FULL,
            content_type=work.content_type,
            platform_content_id=work.note_id,
            warnings=warnings,
        )

    # 下载全部图片并通过本地输入调用视觉模型。
    def _extract_image_text(
        self,
        work: XiaohongshuWork,
        progress: ExtractionProgress | None,
    ) -> tuple[str, tuple[str, ...]]:
        """返回图文正文和警告。"""

        if self._vision is None:
            raise ValueError("处理小红书图文必须配置 KW_VISION_API_KEY/BASE_URL/MODEL")
        with self._temp_workspaces.create("knowwhere-xiaohongshu-images-") as temp_dir:
            # 当前临时目录。
            temp_path = Path(temp_dir)
            # 保持笔记原顺序的本地图片输入。
            images: list[VisionInput] = []
            for index, image_url in enumerate(work.image_urls):
                # 当前图片本地路径。
                image_path = temp_path / f"image-{index}.bin"
                # 媒体响应类型。
                content_type = self._downloader.download(
                    (image_url,), image_path, self._settings.max_image_bytes
                )
                images.append(VisionInput(path=image_path, media_type=content_type))
            self._report(
                progress,
                "xiaohongshu_images_downloaded",
                {"count": len(images)},
            )
            # 视觉模型生成的完整正文。
            body_text = self._vision.describe(tuple(images), work.caption)
            self._report(
                progress,
                "xiaohongshu_vision_completed",
                {"count": len(images)},
            )
        return body_text, ()

    # 下载视频、标准化音频并调用已配置的 ASR。
    def _extract_video(
        self,
        work: XiaohongshuWork,
        progress: ExtractionProgress | None,
    ) -> tuple[str, tuple[str, ...]]:
        """返回视频配文、转录正文和警告。"""

        if not work.video_urls:
            raise ValueError("小红书视频笔记缺少播放地址")
        with self._temp_workspaces.create("knowwhere-xiaohongshu-video-") as temp_dir:
            # 当前临时目录。
            temp_path = Path(temp_dir)
            # 下载视频路径。
            video_path = temp_path / "source-video.mp4"
            self._downloader.download(
                work.video_urls,
                video_path,
                self._settings.max_video_bytes,
            )
            self._report(progress, "xiaohongshu_video_downloaded", {})
            # 单声道 16k 有序音频分段。
            audio_paths = self._audio_extractor.extract_segments(video_path, temp_path)
            self._report(
                progress,
                "xiaohongshu_audio_extracted",
                {
                    "segment_count": len(audio_paths),
                    "audio_bytes": sum(path.stat().st_size for path in audio_paths),
                },
            )

            # 报告单个 ASR 分段完成且不暴露本地路径。
            def segment_completed(segment_index: int, segment_count: int) -> None:
                """保存当前小红书转录进度。"""

                self._report(
                    progress,
                    "xiaohongshu_asr_segment_completed",
                    {"segment_index": segment_index, "segment_count": segment_count},
                )

            # 已按顺序合并的完整视频转录。
            transcription = self._transcriber.transcribe_segments(
                audio_paths,
                segment_completed,
            )
            self._report(progress, "xiaohongshu_asr_completed", {})
        # 配文和语音证据共同组成可分析正文。
        body_text = f"作品配文：\n{work.caption or '无'}\n\n视频转录：\n{transcription.text}"
        return body_text, transcription.warnings

    # 可选进度回调统一空值处理。
    @staticmethod
    def _report(
        progress: ExtractionProgress | None,
        stage: str,
        data: dict[str, object],
    ) -> None:
        """报告不含访问令牌或媒体 URL 的安全阶段进度。"""

        if progress is not None:
            progress(stage, data)
