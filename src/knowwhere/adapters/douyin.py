"""抖音公开图文与视频内容提取器。"""

from __future__ import annotations

import secrets
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from knowwhere.adapters._vendor.f2_abogus import ABogus, BrowserFingerprintGenerator
from knowwhere.application.ports import (
    ArtifactStorePort,
    AsrProviderPort,
    ContentExtractorPort,
    ExtractionProgress,
    VisionProviderPort,
)
from knowwhere.config import DouyinSettings
from knowwhere.domain.models import ContentQuality, ContentType, ExtractedContent

# 抖音网页请求使用与 A-Bogus 指纹一致的浏览器标识。
DOUYIN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
    "Safari/537.36 Edg/130.0.0.0"
)
# 允许参与短链跳转的精确抖音主机名。
DOUYIN_HOSTS = frozenset({"v.douyin.com", "www.iesdouyin.com", "www.douyin.com"})
# ttwid 官方注册地址。
TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
# ttwid 注册请求体来自 F2 对抖音 Web 客户端的兼容实现。
TTWID_REGISTER_BODY = (
    '{"region":"cn","aid":1768,"needFid":false,"service":"www.ixigua.com",'
    '"migrate_info":{"ticket":"","source":"node"},'
    '"cbUrlProtocol":"https","union":true}'
)
# 抖音作品详情接口。
DOUYIN_DETAIL_ENDPOINT = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
# 详情接口会随机校验匿名指纹，有限次重建会话身份可规避瞬时 403。
DOUYIN_DETAIL_MAX_ATTEMPTS = 3


# 解析阶段保留元数据和有序媒体地址，不携带平台原始大对象。
@dataclass(frozen=True, slots=True)
class DouyinWork:
    """抖音作品解析结果。"""

    # 用户提交的原始地址。
    source_url: str
    # 稳定规范地址。
    canonical_url: str
    # 抖音作品 ID。
    aweme_id: str
    # 作品内容类型。
    content_type: ContentType
    # 作品标题。
    title: str
    # 作者昵称。
    author: str | None
    # 作品配文。
    caption: str
    # 发布时间。
    published_at: datetime | None
    # 按作品顺序排列的图片地址。
    image_urls: tuple[str, ...] = ()
    # 视频播放地址。
    video_url: str | None = None
    # 平台声明的视频时长毫秒数。
    duration_milliseconds: int | None = None


# 抖音 Web 解析器负责短链还原、签名请求和字段兼容。
class DouyinWebResolver:
    """解析无需登录即可访问的抖音公开作品。"""

    # 保存可替换 HTTP 客户端。
    def __init__(
        self,
        settings: DouyinSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化解析器。"""

        # 抖音下载限制配置。
        self._settings = settings
        # HTTP 客户端不自动跨任意主机跟随跳转。
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
        )
        # 与签名指纹一致的公共请求头。
        self._headers = {
            "User-Agent": DOUYIN_USER_AGENT,
            "Referer": "https://www.douyin.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    # 解析用户短链并取得作品详情。
    def resolve(self, url: str) -> DouyinWork:
        """返回规范化抖音作品。"""

        # 去除分享文本可能携带的首尾空白。
        source_url = url.strip()
        self._validate_douyin_url(source_url)
        # 短链最终落地地址。
        resolved_url = self._follow_douyin_redirects(source_url)
        # 从受支持路径读取纯数字作品 ID。
        aweme_id, hinted_type = self._extract_aweme_id(resolved_url)
        # 最后一份详情响应供循环结束后统一校验。
        response: httpx.Response | None = None
        for _attempt_index in range(DOUYIN_DETAIL_MAX_ATTEMPTS):
            # 每次尝试使用新的匿名设备 Cookie 和自洽签名。
            ttwid = self._create_ttwid()
            # 签名后的作品详情地址。
            detail_url = self._build_detail_url(aweme_id)
            # 作品详情响应。
            response = self._client.get(
                detail_url,
                headers={**self._headers, "Cookie": f"ttwid={ttwid};"},
            )
            if response.status_code != 403:
                break
        if response is None:
            raise RuntimeError("抖音作品详情请求未执行")
        response.raise_for_status()
        if not response.content:
            raise ValueError("抖音作品详情为空，可能触发了平台访问校验")
        # 平台 JSON 载荷。
        response_data = response.json()
        # 作品详情对象。
        detail = response_data.get("aweme_detail")
        if not isinstance(detail, dict):
            raise ValueError("抖音作品详情响应缺少 aweme_detail")
        return self._to_work(source_url, aweme_id, hinted_type, detail)

    # 在精确主机白名单内手动跟随分享短链。
    def _follow_douyin_redirects(self, url: str) -> str:
        """返回抖音最终作品地址。"""

        # 当前跳转地址。
        current_url = url
        for _redirect_index in range(6):
            # 每一跳都禁用客户端自动跳转。
            response = self._client.get(
                current_url,
                headers=self._headers,
                follow_redirects=False,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                response.raise_for_status()
                return str(response.url)
            # Location 可以是相对地址。
            location = response.headers.get("location")
            if not location:
                raise ValueError("抖音短链跳转缺少 Location")
            # 下一跳绝对地址。
            next_url = urljoin(str(response.url), location)
            self._validate_douyin_url(next_url)
            current_url = next_url
        raise ValueError("抖音短链跳转次数过多")

    # 获取匿名访问作品详情所需的 ttwid。
    def _create_ttwid(self) -> str:
        """注册匿名 Web 设备 Cookie。"""

        # ttwid 注册响应。
        response = self._client.post(
            TTWID_REGISTER_URL,
            content=TTWID_REGISTER_BODY,
            headers=self._headers,
        )
        response.raise_for_status()
        # Cookie 值只保留在当前解析调用中。
        ttwid = response.cookies.get("ttwid")
        if not ttwid:
            raise ValueError("抖音匿名设备注册未返回 ttwid")
        return str(ttwid)

    # 生成与浏览器指纹一致的作品详情签名地址。
    @staticmethod
    def _build_detail_url(aweme_id: str) -> str:
        """生成 A-Bogus 签名详情地址。"""

        # 参数顺序必须参与签名，保持与官方 Web 客户端兼容。
        params: dict[str, object] = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "pc_client_type": 1,
            "publish_video_strategy_type": 2,
            "pc_libra_divert": "Windows",
            "version_code": "290100",
            "version_name": "29.1.0",
            "cookie_enabled": "true",
            "screen_width": 1920,
            "screen_height": 1080,
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Edge",
            "browser_version": "130.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "engine_version": "130.0.0.0",
            "os_name": "Windows",
            "os_version": "10",
            "cpu_core_num": 12,
            "device_memory": 8,
            "platform": "PC",
            "downlink": 10,
            "effective_type": "4g",
            "round_trip_time": 100,
            "msToken": f"{secrets.token_urlsafe(128)[:146]}==",
            "aweme_id": aweme_id,
        }
        # 与上游算法约定一致的不转义查询字符串。
        query = "&".join(f"{key}={value}" for key, value in params.items())
        # 随机但自洽的浏览器指纹。
        browser_fingerprint = BrowserFingerprintGenerator.generate_fingerprint("Edge")
        # A-Bogus 签名结果。
        a_bogus = ABogus(
            fp=browser_fingerprint,
            user_agent=DOUYIN_USER_AGENT,
        ).generate_abogus(query)[1]
        return f"{DOUYIN_DETAIL_ENDPOINT}?{query}&a_bogus={a_bogus}"

    # 把平台 JSON 映射为稳定的内部工作对象。
    def _to_work(
        self,
        source_url: str,
        aweme_id: str,
        hinted_type: ContentType,
        detail: dict[str, Any],
    ) -> DouyinWork:
        """解析作品元数据和媒体清单。"""

        # 作品配文。
        caption = str(detail.get("desc") or "").strip()
        # 作者对象。
        author_data = detail.get("author")
        # 作者昵称。
        author = (
            str(author_data.get("nickname") or "").strip() or None
            if isinstance(author_data, dict)
            else None
        )
        # 有序图片列表。
        image_urls = self._extract_image_urls(detail)
        # JSON 中存在图片时以事实字段为准，避免路径提示失真。
        content_type = ContentType.IMAGE_TEXT if image_urls else hinted_type
        # 视频播放地址。
        video_url = None if image_urls else self._extract_video_url(detail)
        if content_type is ContentType.VIDEO and video_url is None:
            raise ValueError("抖音视频作品缺少可用播放地址")
        # 用户可读标题优先使用配文首行。
        title = (caption.splitlines()[0].strip()[:200] if caption else "") or (
            f"抖音{'图文' if content_type is ContentType.IMAGE_TEXT else '视频'} {aweme_id}"
        )
        # 秒级 Unix 发布时间。
        create_time = detail.get("create_time")
        # 安全转换后的发布时间。
        published_at = (
            datetime.fromtimestamp(int(create_time), UTC)
            if isinstance(create_time, (int, float, str)) and str(create_time).isdigit()
            else None
        )
        # 规范地址根据真实媒体类型选择路径。
        path_type = "note" if content_type is ContentType.IMAGE_TEXT else "video"
        # 平台视频时长对象。
        video_data = detail.get("video")
        # 视频时长毫秒数。
        duration_value = video_data.get("duration") if isinstance(video_data, dict) else None
        # 安全转换后的视频时长。
        duration_milliseconds = (
            int(duration_value)
            if isinstance(duration_value, (int, float, str)) and str(duration_value).isdigit()
            else None
        )
        return DouyinWork(
            source_url=source_url,
            canonical_url=f"https://www.douyin.com/{path_type}/{aweme_id}",
            aweme_id=aweme_id,
            content_type=content_type,
            title=title,
            author=author,
            caption=caption,
            published_at=published_at,
            image_urls=image_urls,
            video_url=video_url,
            duration_milliseconds=duration_milliseconds,
        )

    # 提取并去重全部图片地址。
    def _extract_image_urls(self, detail: dict[str, Any]) -> tuple[str, ...]:
        """返回按原作品顺序排列的图片地址。"""

        # 平台图片对象列表。
        images = detail.get("images")
        if not isinstance(images, list):
            return ()
        if len(images) > self._settings.max_images:
            raise ValueError("抖音图文图片数量超过配置上限，拒绝不完整入库")
        # 保序图片地址。
        result: list[str] = []
        for image_data in images:
            if not isinstance(image_data, dict):
                continue
            # 当前图片的 CDN 候选地址。
            candidates = image_data.get("url_list")
            if not isinstance(candidates, list):
                continue
            # 第一条有效 HTTPS 地址。
            image_url = next(
                (str(value) for value in candidates if str(value).startswith("https://")),
                None,
            )
            if image_url and image_url not in result:
                result.append(image_url)
        return tuple(result)

    # 从视频字段选择第一条 HTTPS 播放地址。
    @staticmethod
    def _extract_video_url(detail: dict[str, Any]) -> str | None:
        """返回视频播放地址。"""

        # 视频对象。
        video_data = detail.get("video")
        if not isinstance(video_data, dict):
            return None
        # 默认播放地址对象。
        play_address = video_data.get("play_addr")
        if not isinstance(play_address, dict):
            return None
        # CDN 候选地址。
        candidates = play_address.get("url_list")
        if not isinstance(candidates, list):
            return None
        return next(
            (str(value) for value in candidates if str(value).startswith("https://")),
            None,
        )

    # 只接受抖音官方 HTTPS 地址。
    @staticmethod
    def _validate_douyin_url(url: str) -> None:
        """校验抖音 URL 主机和协议。"""

        # URL 解析结果。
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in DOUYIN_HOSTS:
            raise ValueError("抖音链接必须使用受支持的官方 HTTPS 主机")
        if parts.username or parts.password or parts.port is not None:
            raise ValueError("抖音链接不能包含用户信息或自定义端口")

    # 从 video、note 或 share 路径读取作品 ID。
    @staticmethod
    def _extract_aweme_id(url: str) -> tuple[str, ContentType]:
        """返回作品 ID 和路径提示类型。"""

        # 路径片段。
        segments = [segment for segment in urlsplit(url).path.split("/") if segment]
        for index, segment in enumerate(segments[:-1]):
            if segment not in {"video", "note"}:
                continue
            # 紧随类型片段的作品 ID。
            aweme_id = segments[index + 1]
            if aweme_id.isdigit():
                # 路径提示的内容类型。
                content_type = (
                    ContentType.IMAGE_TEXT if segment == "note" else ContentType.VIDEO
                )
                return aweme_id, content_type
        raise ValueError("抖音链接中没有可识别的作品 ID")


# 媒体下载器执行大小限制并保留抖音 Referer。
class DouyinMediaDownloader:
    """把短时 CDN 媒体流式写入本地临时文件。"""

    # 保存请求配置和可替换客户端。
    def __init__(
        self,
        settings: DouyinSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化媒体下载器。"""

        # 下载限制配置。
        self._settings = settings
        # 允许媒体 CDN 自身执行 HTTPS 跳转。
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": DOUYIN_USER_AGENT, "Referer": "https://www.douyin.com/"},
        )

    # 流式下载媒体并在超限前中止。
    def download(self, url: str, destination: Path, max_bytes: int) -> str:
        """返回响应 Content-Type。"""

        if not url.startswith("https://"):
            raise ValueError("抖音媒体地址必须使用 HTTPS")
        # 已写入字节数。
        written_bytes = 0
        with self._client.stream("GET", url) as response:
            response.raise_for_status()
            # 服务器声明的内容长度。
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("抖音媒体文件超过配置大小限制")
            with destination.open("wb") as output_file:
                for chunk in response.iter_bytes():
                    written_bytes += len(chunk)
                    if written_bytes > max_bytes:
                        raise ValueError("抖音媒体文件超过配置大小限制")
                    output_file.write(chunk)
            # 媒体响应类型。
            content_type = str(response.headers.get("content-type", ""))
        if written_bytes == 0:
            raise ValueError("抖音媒体下载结果为空")
        return content_type.split(";", 1)[0].strip().lower()


# FFmpeg 只负责把视频音轨标准化并切成腾讯 ASR 单任务安全分段。
class FfmpegAudioExtractor:
    """执行本地确定性音频标准化。"""

    # 每个音频分段四小时，低于腾讯 URL 识别五小时限制并保留容错空间。
    SEGMENT_SECONDS = 4 * 60 * 60

    # 保存可执行文件路径。
    def __init__(self, ffmpeg_path: str) -> None:
        """初始化 FFmpeg 适配器。"""

        self._ffmpeg_path = ffmpeg_path

    # 从视频提取一个或多个 ASR 兼容音频分段。
    def extract_segments(self, video_path: Path, output_directory: Path) -> tuple[Path, ...]:
        """生成有序单声道 16k MP3 分段。"""

        # 分段输出模板。
        output_pattern = output_directory / "audio-%04d.mp3"
        # 不经 Shell 执行的固定参数命令。
        command = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            "-f",
            "segment",
            "-segment_time",
            str(self.SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            str(output_pattern),
        ]
        try:
            # FFmpeg 子进程结果。
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=900,
            )
        except FileNotFoundError as error:
            raise RuntimeError("未找到 FFmpeg，请配置 KW_FFMPEG_PATH") from error
        # FFmpeg 生成的有序音频分段。
        audio_paths = tuple(sorted(output_directory.glob("audio-*.mp3")))
        if (
            result.returncode != 0
            or not audio_paths
            or any(path.stat().st_size == 0 for path in audio_paths)
        ):
            # 只保留末尾错误，不回显媒体地址或完整命令。
            error_tail = result.stderr.strip()[-1000:]
            raise RuntimeError(f"FFmpeg 音频提取失败: {error_tail}")
        return audio_paths


# 抖音内容提取器编排解析、媒体暂存、视觉理解或 ASR。
class DouyinContentExtractor(ContentExtractorPort):
    """把抖音图文与视频统一转换为 ExtractedContent。"""

    # 注入所有副作用端口。
    def __init__(
        self,
        settings: DouyinSettings,
        resolver: DouyinWebResolver,
        downloader: DouyinMediaDownloader,
        artifact_store: ArtifactStorePort,
        asr: AsrProviderPort,
        vision: VisionProviderPort | None,
        audio_extractor: FfmpegAudioExtractor,
    ) -> None:
        """初始化抖音提取器。"""

        # 抖音媒体限制。
        self._settings = settings
        # 作品详情解析器。
        self._resolver = resolver
        # CDN 媒体下载器。
        self._downloader = downloader
        # 私有临时对象存储。
        self._artifact_store = artifact_store
        # 录音文件识别供应商。
        self._asr = asr
        # 可选视觉模型。
        self._vision = vision
        # 本地音频标准化器。
        self._audio_extractor = audio_extractor

    # 按真实作品类型执行唯一分支。
    def extract(
        self,
        url: str,
        progress: ExtractionProgress | None = None,
    ) -> ExtractedContent:
        """提取抖音图文或视频正文。"""

        # 解析后的作品清单。
        work = self._resolver.resolve(url)
        self._report(
            progress,
            "douyin_resolved",
            {"aweme_id": work.aweme_id, "content_type": work.content_type.value},
        )
        if work.content_type is ContentType.IMAGE_TEXT:
            # 图文视觉理解正文与清理警告。
            body_text, warnings = self._extract_image_text(work, progress)
        else:
            # 视频语音转录正文与清理警告。
            body_text, warnings = self._extract_video(work, progress)
        return ExtractedContent(
            source_url=work.source_url,
            canonical_url=work.canonical_url,
            platform="抖音",
            title=work.title,
            author=work.author,
            body_text=body_text,
            published_at=work.published_at,
            quality=ContentQuality.FULL,
            content_type=work.content_type,
            platform_content_id=work.aweme_id,
            warnings=warnings,
        )

    # 下载全部图片、暂存 COS、调用视觉模型并清理。
    def _extract_image_text(
        self,
        work: DouyinWork,
        progress: ExtractionProgress | None,
    ) -> tuple[str, tuple[str, ...]]:
        """返回图文正文和警告。"""

        if self._vision is None:
            raise ValueError("处理抖音图文必须配置 KW_VISION_API_KEY/BASE_URL/MODEL")
        # 已上传 COS 对象引用。
        artifact_refs: list[str] = []
        # 临时对象清理是否失败。
        cleanup_failed = False
        try:
            with tempfile.TemporaryDirectory(prefix="knowwhere-douyin-images-") as temp_dir:
                # 当前临时目录。
                temp_path = Path(temp_dir)
                for index, image_url in enumerate(work.image_urls):
                    # 当前图片本地路径。
                    image_path = temp_path / f"image-{index}.bin"
                    # 媒体响应类型。
                    content_type = self._downloader.download(
                        image_url,
                        image_path,
                        self._settings.max_image_bytes,
                    )
                    # COS 对象后缀。
                    suffix = self._image_suffix(content_type)
                    # 当前对象引用。
                    artifact_ref = self._artifact_store.put(image_path.read_bytes(), suffix)
                    artifact_refs.append(artifact_ref)
            self._report(progress, "douyin_images_uploaded", {"count": len(artifact_refs)})
            # 有序短时图片地址。
            image_download_urls = tuple(
                self._artifact_store.create_download_url(ref) for ref in artifact_refs
            )
            # 视觉模型完整正文。
            body_text = self._vision.describe(image_download_urls, work.caption)
            self._report(progress, "douyin_vision_completed", {"count": len(artifact_refs)})
        finally:
            for artifact_ref in artifact_refs:
                try:
                    self._artifact_store.delete(artifact_ref)
                except Exception:
                    cleanup_failed = True
        # 清理失败作为可观察警告，不丢弃已成功生成的正文。
        warnings = ("TEMP_ARTIFACT_CLEANUP_FAILED",) if cleanup_failed else ()
        return body_text, warnings

    # 下载视频、标准化音频、暂存 COS、调用腾讯 ASR 并清理。
    def _extract_video(
        self,
        work: DouyinWork,
        progress: ExtractionProgress | None,
    ) -> tuple[str, tuple[str, ...]]:
        """返回视频配文和转录正文。"""

        if work.video_url is None:
            raise ValueError("抖音视频作品缺少播放地址")
        # 当前 COS 音频对象引用。
        artifact_ref: str | None = None
        # 临时对象清理是否失败。
        cleanup_failed = False
        # 按原视频时间顺序排列的分段转录。
        transcripts: list[str] = []
        try:
            with tempfile.TemporaryDirectory(prefix="knowwhere-douyin-video-") as temp_dir:
                # 当前临时目录。
                temp_path = Path(temp_dir)
                # 下载视频路径。
                video_path = temp_path / "source-video.mp4"
                self._downloader.download(
                    work.video_url,
                    video_path,
                    self._settings.max_video_bytes,
                )
                self._report(progress, "douyin_video_downloaded", {})
                # 单声道 16k 有序音频分段。
                audio_paths = self._audio_extractor.extract_segments(video_path, temp_path)
                self._report(
                    progress,
                    "douyin_audio_extracted",
                    {
                        "segment_count": len(audio_paths),
                        "audio_bytes": sum(path.stat().st_size for path in audio_paths),
                    },
                )
                for segment_index, audio_path in enumerate(audio_paths):
                    artifact_ref = self._artifact_store.put(audio_path.read_bytes(), ".mp3")
                    self._report(
                        progress,
                        "douyin_audio_uploaded",
                        {"segment_index": segment_index, "segment_count": len(audio_paths)},
                    )
                    # 腾讯 ASR 可读的私有 COS 短时 URL。
                    audio_url = self._artifact_store.create_download_url(artifact_ref)
                    # 当前音频分段转录。
                    transcripts.append(self._asr.transcribe(audio_url))
                    try:
                        self._artifact_store.delete(artifact_ref)
                    except Exception:
                        cleanup_failed = True
                    artifact_ref = None
                    self._report(
                        progress,
                        "douyin_asr_segment_completed",
                        {"segment_index": segment_index, "segment_count": len(audio_paths)},
                    )
            self._report(progress, "douyin_asr_completed", {})
        finally:
            if artifact_ref is not None:
                try:
                    self._artifact_store.delete(artifact_ref)
                except Exception:
                    cleanup_failed = True
        # 配文和语音证据共同组成可分析正文。
        # 分段边界只用于处理，不污染最终可检索正文。
        transcript = "\n".join(transcripts)
        body_text = f"作品配文：\n{work.caption or '无'}\n\n视频转录：\n{transcript}"
        # 清理失败作为可观察警告。
        warnings = ("TEMP_ARTIFACT_CLEANUP_FAILED",) if cleanup_failed else ()
        return body_text, warnings

    # 根据响应类型选择视觉模型可识别后缀。
    @staticmethod
    def _image_suffix(content_type: str) -> str:
        """返回安全图片后缀。"""

        # 常见图片 MIME 到扩展名映射。
        suffixes = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        return suffixes.get(content_type, ".jpg")

    # 可选进度回调统一空值处理。
    @staticmethod
    def _report(
        progress: ExtractionProgress | None,
        stage: str,
        data: dict[str, object],
    ) -> None:
        """报告安全阶段进度。"""

        if progress is not None:
            progress(stage, data)
