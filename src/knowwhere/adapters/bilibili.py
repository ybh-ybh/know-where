"""B站公开视频内容提取器。"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from knowwhere.adapters.douyin import FfmpegAudioExtractor
from knowwhere.adapters.local_temp_storage import LocalTempWorkspaceFactory
from knowwhere.application.media import AudioTranscriptionService
from knowwhere.application.ports import (
    ContentExtractorPort,
    ExtractionProgress,
)
from knowwhere.config import BilibiliSettings
from knowwhere.domain.models import ContentQuality, ContentType, ExtractedContent

# B站网页与 CDN 请求使用稳定桌面浏览器标识。
BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
    "Safari/537.36"
)
# 允许用户提交和短链跳转到的精确官方主机名。
BILIBILI_HOSTS = frozenset(
    {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
)
# B站公开稿件详情接口。
BILIBILI_VIEW_ENDPOINT = "https://api.bilibili.com/x/web-interface/view"
# B站匿名访问也会返回 WBI 图片密钥的导航接口。
BILIBILI_NAV_ENDPOINT = "https://api.bilibili.com/x/web-interface/nav"
# B站公开网页 WBI 播放信息接口。
BILIBILI_PLAYURL_ENDPOINT = "https://api.bilibili.com/x/player/wbi/playurl"
# 分享短链最多允许的安全跳转次数。
BILIBILI_MAX_REDIRECTS = 6
# WBI 图片密钥混排顺序；该协议行为由多个成熟开源提取器共同验证。
WBI_MIXIN_KEY_ENC_TAB = (
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
)


# 解析阶段只保留稳定元数据与短期音频候选地址。
@dataclass(frozen=True, slots=True)
class BilibiliWork:
    """B站单个视频分P的解析结果。"""

    # 用户提交的原始地址。
    source_url: str
    # 稳定规范地址。
    canonical_url: str
    # 视频 BV 号。
    bvid: str
    # 当前分P的 CID。
    cid: str
    # 当前分P序号。
    page_number: int
    # 跨链接形式去重的作品标识。
    platform_content_id: str
    # 当前视频标题。
    title: str
    # UP 主名称。
    author: str | None
    # 视频简介。
    description: str
    # 发布时间。
    published_at: datetime | None
    # 当前分P时长秒数。
    duration_seconds: int | None
    # 首选音频及同轨备用 CDN 地址。
    audio_urls: tuple[str, ...]
    # 平台声明的音频 MIME 类型。
    audio_mime_type: str


# 媒体候选全部发生网络错误时允许上层刷新一次播放信息。
class BilibiliMediaUnavailableError(RuntimeError):
    """表示当前一组B站短期媒体地址均不可用。"""


# B站 Web 解析器负责短链、公开元数据和 DASH 音频轨选择。
class BilibiliWebResolver:
    """解析无需登录即可访问的B站公开视频。"""

    # 保存请求配置与可替换客户端。
    def __init__(
        self,
        settings: BilibiliSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化解析器。"""

        # B站媒体处理限制。
        self._settings = settings
        # API 客户端不自动跨任意主机跟随跳转。
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
        )
        # 公开 API 和页面请求头。
        self._headers = {
            "User-Agent": BILIBILI_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://www.bilibili.com",
        }

    # 解析用户地址并取得当前分P的最佳匿名音频轨。
    def resolve(self, url: str) -> BilibiliWork:
        """返回规范化B站视频。"""

        # 去除分享文本可能携带的首尾空白。
        source_url = url.strip()
        self._validate_bilibili_url(source_url)
        # 标准视频地址或安全还原后的短链地址。
        resolved_url = self._resolve_video_url(source_url)
        # 地址中的 BV 号。
        bvid = self._extract_bvid(resolved_url)
        # 用户选择的分P序号。
        page_number = self._extract_page_number(resolved_url)
        # 视频详情载荷。
        detail = self._request_api(
            BILIBILI_VIEW_ENDPOINT,
            {"bvid": bvid},
            "视频详情请求",
            referer=f"https://www.bilibili.com/video/{bvid}/",
        )
        return self._to_work(source_url, bvid, page_number, detail)

    # 标准地址直接使用，b23.tv 地址只在官方主机白名单内跳转。
    def _resolve_video_url(self, url: str) -> str:
        """返回包含 BV 号的官方视频地址。"""

        try:
            self._extract_bvid(url)
            return url
        except ValueError:
            pass
        if (urlsplit(url).hostname or "").lower() != "b23.tv":
            raise ValueError("B站链接中没有可识别的 BV 号")

        # 当前短链地址。
        current_url = url
        for _redirect_index in range(BILIBILI_MAX_REDIRECTS):
            # 当前跳转响应。
            response = self._client.get(
                current_url,
                headers=self._headers,
                follow_redirects=False,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                response.raise_for_status()
                self._extract_bvid(str(response.url))
                return str(response.url)
            # 下一跳 Location。
            location = response.headers.get("location")
            if not location:
                raise ValueError("B站短链跳转缺少 Location")
            # 规范化后的下一跳绝对地址。
            next_url = str(urljoin(str(response.url), str(location)))
            self._validate_bilibili_url(next_url)
            try:
                self._extract_bvid(next_url)
                return next_url
            except ValueError:
                current_url = next_url
        raise ValueError("B站短链跳转次数过多")

    # 把视频详情与播放信息映射为稳定工作对象。
    def _to_work(
        self,
        source_url: str,
        requested_bvid: str,
        requested_page_number: int | None,
        detail: dict[str, Any],
    ) -> BilibiliWork:
        """解析视频元数据与最佳 DASH 音频轨。"""

        # API 返回的规范 BV 号。
        bvid = str(detail.get("bvid") or "").strip()
        if bvid != requested_bvid:
            raise ValueError("B站视频详情返回了不匹配的 BV 号")
        # 视频分P列表。
        pages = detail.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ValueError("B站视频详情缺少分P信息")
        if len(pages) > 1 and requested_page_number is None:
            raise ValueError("B站多P视频必须通过 p 参数明确选择分P")
        # 单P视频缺省选择唯一页面。
        page_number = requested_page_number or 1
        # 与用户请求序号匹配的分P。
        page = next(
            (
                item
                for item in pages
                if isinstance(item, dict) and self._safe_int(item.get("page")) == page_number
            ),
            None,
        )
        if page is None:
            raise ValueError(f"B站视频不存在第 {page_number} 个分P")
        # 当前分P CID。
        cid = str(page.get("cid") or "").strip()
        if not cid.isdigit():
            raise ValueError("B站视频分P缺少有效 CID")
        # 当前页面的播放信息。
        play_data = self._request_play_api(
            {
                "bvid": bvid,
                "cid": cid,
                "qn": "64",
                "fnval": "4048",
                "fnver": "0",
                "fourk": "1",
                "try_look": "1",
            },
            referer=f"https://www.bilibili.com/video/{bvid}/?p={page_number}",
        )
        # 最佳匿名 DASH 音频轨及其 CDN 回退地址。
        audio_urls, audio_mime_type = self._extract_best_audio(play_data)
        # 视频主标题。
        main_title = str(detail.get("title") or "").strip() or f"B站视频 {bvid}"
        # 当前分P标题。
        part_title = str(page.get("part") or "").strip()
        # 多P视频使用主标题和分P标题共同避免歧义。
        title = (
            f"{main_title} - {part_title}"
            if len(pages) > 1 and part_title and part_title != main_title
            else main_title
        )
        # UP 主对象。
        owner = detail.get("owner")
        # UP 主名称。
        author = (
            str(owner.get("name") or "").strip() or None if isinstance(owner, dict) else None
        )
        # 秒级 Unix 发布时间。
        pubdate = self._safe_int(detail.get("pubdate"))
        # 当前分P时长。
        duration_seconds = self._safe_int(page.get("duration"))
        # 多P视频的稳定地址明确保留分P序号。
        canonical_url = f"https://www.bilibili.com/video/{bvid}"
        if len(pages) > 1:
            canonical_url = f"{canonical_url}?p={page_number}"
        # 多P视频的去重标识需要区分每个独立媒体分P。
        platform_content_id = f"{bvid}:p{page_number}" if len(pages) > 1 else bvid
        return BilibiliWork(
            source_url=source_url,
            canonical_url=canonical_url,
            bvid=bvid,
            cid=cid,
            page_number=page_number,
            platform_content_id=platform_content_id,
            title=title,
            author=author,
            description=str(detail.get("desc") or "").strip(),
            published_at=datetime.fromtimestamp(pubdate, UTC) if pubdate is not None else None,
            duration_seconds=duration_seconds,
            audio_urls=audio_urls,
            audio_mime_type=audio_mime_type,
        )

    # 从常规 DASH 音频列表选择最高带宽轨道。
    @classmethod
    def _extract_best_audio(cls, play_data: dict[str, Any]) -> tuple[tuple[str, ...], str]:
        """返回最佳音频轨的主地址与备用地址。"""

        # DASH 播放信息。
        dash = play_data.get("dash")
        # 常规匿名音频轨列表。
        audio_tracks = dash.get("audio") if isinstance(dash, dict) else None
        if not isinstance(audio_tracks, list) or not audio_tracks:
            raise ValueError("B站播放信息缺少可用 DASH 音频轨")
        # 仅保留字段结构有效的音频轨。
        valid_tracks = [item for item in audio_tracks if isinstance(item, dict)]
        if not valid_tracks:
            raise ValueError("B站播放信息缺少有效 DASH 音频轨")
        # 匿名接口返回轨道中带宽最高的一条。
        best_track = max(valid_tracks, key=lambda item: cls._safe_int(item.get("bandwidth")) or 0)
        # 平台同时返回驼峰和下划线兼容字段。
        base_url = str(best_track.get("base_url") or best_track.get("baseUrl") or "").strip()
        # 同一音频轨的备用 CDN 列表。
        backup_values = best_track.get("backup_url") or best_track.get("backupUrl") or []
        # 未校验的候选地址。
        raw_urls = [base_url]
        if isinstance(backup_values, list):
            raw_urls.extend(str(value).strip() for value in backup_values)
        # 保序、去重后的 HTTPS 地址。
        audio_urls: list[str] = []
        for candidate_url in raw_urls:
            # 候选地址结构。
            parts = urlsplit(candidate_url)
            if parts.scheme == "https" and parts.hostname and candidate_url not in audio_urls:
                audio_urls.append(candidate_url)
        if not audio_urls:
            raise ValueError("B站 DASH 音频轨缺少有效 HTTPS 地址")
        # 音频 MIME 类型。
        audio_mime_type = str(
            best_track.get("mime_type") or best_track.get("mimeType") or "application/octet-stream"
        ).strip()
        return tuple(audio_urls), audio_mime_type

    # 请求B站公开 API 并统一校验业务状态码。
    def _request_api(
        self,
        endpoint: str,
        params: dict[str, str],
        operation: str,
        referer: str,
    ) -> dict[str, Any]:
        """返回业务成功响应中的 data 对象。"""

        # 当前公开 API 响应。
        response = self._client.get(
            endpoint,
            params=params,
            headers={**self._headers, "Referer": referer},
        )
        response.raise_for_status()
        # JSON 根对象。
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"B站{operation}响应不是 JSON 对象")
        # B站业务状态码。
        code = self._safe_int(payload.get("code"))
        if code != 0:
            # 平台公开错误消息不含请求凭据。
            message = str(payload.get("message") or "未知错误").strip()
            raise ValueError(f"B站{operation}失败（code={code}）: {message}")
        # 业务数据对象。
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"B站{operation}响应缺少 data")
        return data

    # 获取匿名 WBI 密钥并请求签名播放信息。
    def _request_play_api(
        self,
        params: dict[str, str],
        referer: str,
    ) -> dict[str, Any]:
        """返回 WBI 播放接口中的 data 对象。"""

        # 匿名导航响应的业务码可能是 -101，但仍包含可用 WBI 图片密钥。
        nav_payload = self._request_payload(
            BILIBILI_NAV_ENDPOINT,
            {},
            referer="https://www.bilibili.com/",
        )
        # 导航业务数据。
        nav_data = nav_payload.get("data")
        # WBI 图片对象。
        wbi_img = nav_data.get("wbi_img") if isinstance(nav_data, dict) else None
        if not isinstance(wbi_img, dict):
            raise ValueError("B站导航响应缺少 WBI 图片密钥")
        # 图片主密钥地址。
        img_url = str(wbi_img.get("img_url") or "").strip()
        # 图片副密钥地址。
        sub_url = str(wbi_img.get("sub_url") or "").strip()
        # 当前混排密钥。
        mixin_key = self._build_mixin_key(img_url, sub_url)
        # 带时间戳和摘要签名的播放参数。
        signed_params = self._sign_wbi({**params, **self._build_device_params()}, mixin_key)
        return self._request_api(
            BILIBILI_PLAYURL_ENDPOINT,
            signed_params,
            "播放信息请求",
            referer=referer,
        )

    # 请求任意B站 JSON 接口，但不提前解释业务状态码。
    def _request_payload(
        self,
        endpoint: str,
        params: dict[str, str],
        referer: str,
    ) -> dict[str, Any]:
        """返回经过 HTTP 与 JSON 结构校验的根对象。"""

        # 当前接口响应。
        response = self._client.get(
            endpoint,
            params=params,
            headers={**self._headers, "Referer": referer},
        )
        response.raise_for_status()
        # JSON 根对象。
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("B站接口响应不是 JSON 对象")
        return payload

    # 从两张 WBI 图片文件名构造当前混排密钥。
    @staticmethod
    def _build_mixin_key(img_url: str, sub_url: str) -> str:
        """返回当前 WBI 请求使用的 32 字符混排密钥。"""

        # 图片主密钥。
        img_key = Path(urlsplit(img_url).path).stem
        # 图片副密钥。
        sub_key = Path(urlsplit(sub_url).path).stem
        # 两段原始密钥必须足以覆盖协议混排索引。
        raw_key = f"{img_key}{sub_key}"
        if len(raw_key) < 64:
            raise ValueError("B站 WBI 图片密钥长度无效")
        # 固定协议顺序混排后的前 32 个字符。
        return "".join(raw_key[index] for index in WBI_MIXIN_KEY_ENC_TAB)[:32]

    # 构造网页播放器用于风控校验的匿名设备参数。
    @staticmethod
    def _build_device_params() -> dict[str, str]:
        """返回不含持久标识和用户 Cookie 的临时播放器指纹。"""

        # 模拟屏幕计算使用的随机扰动。
        screen_random = secrets.randbelow(114)
        # 模拟滚动位置计算使用的随机扰动。
        offset_random = secrets.randbelow(514)
        # 1920x1080 屏幕对应的编码尺寸值。
        screen_values = [
            2 * 1920 + 2 * 1080 + 3 * screen_random,
            4 * 1920 - 1080 + screen_random,
            screen_random,
        ]
        # 轻微页面滚动对应的编码偏移值。
        offset_values = [
            3 * 10 + 2 * 10 + offset_random,
            4 * 10 - 4 * 10 + 2 * offset_random,
            offset_random,
        ]
        # 临时随机字节只参与单次请求，不作为跨任务设备标识。
        image_random = base64.b64encode(secrets.token_bytes(24)).decode().rstrip("=")
        # 封面临时随机字节。
        cover_random = base64.b64encode(secrets.token_bytes(48)).decode().rstrip("=")
        # 平台要求紧凑 JSON，空交互列表不伪造用户点击行为。
        interaction = json.dumps(
            {"ds": [], "wh": screen_values, "of": offset_values},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return {
            "dm_img_list": "[]",
            "dm_img_str": image_random,
            "dm_cover_img_str": cover_random,
            "dm_img_inter": interaction,
        }

    # 对播放参数排序、过滤并生成 WBI 摘要。
    @staticmethod
    def _sign_wbi(params: dict[str, str], mixin_key: str) -> dict[str, str]:
        """返回包含 wts 与 w_rid 的签名参数。"""

        # 参与签名的秒级时间戳。
        wts = str(int(time.time()))
        # WBI 规则要求剔除的特殊字符。
        forbidden_characters = "!'()*"
        # 排序前的完整参数。
        unsigned_params = {**params, "wts": wts}
        # 过滤并按键排序后的参数。
        filtered_params = {
            key: "".join(
                character
                for character in str(value)
                if character not in forbidden_characters
            )
            for key, value in sorted(unsigned_params.items())
        }
        # 与实际请求编码一致的有序查询字符串。
        query = urlencode(filtered_params)
        # WBI MD5 摘要。
        w_rid = hashlib.md5(f"{query}{mixin_key}".encode()).hexdigest()
        return {**filtered_params, "w_rid": w_rid}

    # 只接受B站官方 HTTPS 地址。
    @staticmethod
    def _validate_bilibili_url(url: str) -> None:
        """校验B站 URL 主机、协议和用户信息。"""

        # URL 解析结果。
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in BILIBILI_HOSTS:
            raise ValueError("B站链接必须使用受支持的官方 HTTPS 主机")
        if parts.username or parts.password or parts.port is not None:
            raise ValueError("B站链接不能包含用户信息或自定义端口")

    # 从 /video/{bvid} 路径读取区分大小写的 BV 号。
    @staticmethod
    def _extract_bvid(url: str) -> str:
        """返回视频 BV 号。"""

        # URL 路径片段。
        segments = [segment for segment in urlsplit(url).path.split("/") if segment]
        for index, segment in enumerate(segments[:-1]):
            if segment != "video":
                continue
            # video 后的 BV 候选值。
            bvid = segments[index + 1]
            if len(bvid) == 12 and bvid.startswith("BV") and bvid[2:].isalnum():
                return bvid
        raise ValueError("B站链接中没有可识别的 BV 号")

    # 从查询字符串读取单个正整数分P序号。
    @staticmethod
    def _extract_page_number(url: str) -> int | None:
        """返回用户选择的分P序号，未提供时返回空值。"""

        # 查询字符串映射。
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        # 分P参数列表。
        page_values = query.get("p")
        if page_values is None:
            return None
        if len(page_values) != 1 or not page_values[0].isdigit():
            raise ValueError("B站分P参数 p 必须是单个正整数")
        # 数字分P序号。
        page_number = int(page_values[0])
        if page_number < 1:
            raise ValueError("B站分P参数 p 必须是单个正整数")
        return page_number

    # 把平台数字字段安全转换为整数。
    @staticmethod
    def _safe_int(value: object) -> int | None:
        """返回可转换的整数，否则返回空值。"""

        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


# B站媒体下载器执行大小限制、CDN 回退并保留 Referer。
class BilibiliMediaDownloader:
    """把短时 DASH 音频流式写入本地临时文件。"""

    # 保存请求配置与可替换客户端。
    def __init__(
        self,
        settings: BilibiliSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化媒体下载器。"""

        # B站媒体处理限制。
        self._settings = settings
        # CDN 客户端允许 HTTPS 节点自行跳转。
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": BILIBILI_USER_AGENT, "Referer": "https://www.bilibili.com/"},
        )

    # 按平台顺序尝试首选和备用 CDN 地址。
    def download(self, urls: tuple[str, ...], destination: Path, max_bytes: int) -> str:
        """下载首个可用音频地址并返回响应 Content-Type。"""

        if not urls:
            raise ValueError("B站音频候选地址为空")
        # 最近一次 CDN 传输错误，仅用作异常因果链。
        last_error: httpx.HTTPError | None = None
        for media_url in urls:
            # 当前媒体地址结构。
            parts = urlsplit(media_url)
            if parts.scheme != "https" or not parts.hostname:
                continue
            try:
                return self._download_one(media_url, destination, max_bytes)
            except httpx.HTTPError as error:
                last_error = error
                # 失败节点可能已写入部分内容，回退前清除精确临时文件。
                destination.unlink(missing_ok=True)
        raise BilibiliMediaUnavailableError("B站音频候选地址均下载失败") from last_error

    # 流式下载单个 CDN 音频并在超限前中止。
    def _download_one(self, url: str, destination: Path, max_bytes: int) -> str:
        """返回单个候选地址的响应 Content-Type。"""

        # 已写入字节数。
        written_bytes = 0
        with self._client.stream("GET", url) as response:
            response.raise_for_status()
            # 服务器声明的内容长度。
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise ValueError("B站音频文件超过配置大小限制")
            with destination.open("wb") as output_file:
                for chunk in response.iter_bytes():
                    written_bytes += len(chunk)
                    if written_bytes > max_bytes:
                        raise ValueError("B站音频文件超过配置大小限制")
                    output_file.write(chunk)
            # 媒体响应类型。
            content_type = str(response.headers.get("content-type", ""))
        if written_bytes == 0:
            raise ValueError("B站音频下载结果为空")
        return content_type.split(";", 1)[0].strip().lower()


# B站内容提取器复用本地音频标准化与 ASR 端口。
class BilibiliContentExtractor(ContentExtractorPort):
    """把B站公开视频转换为 ExtractedContent。"""

    # 注入平台解析和视频转录所需副作用端口。
    def __init__(
        self,
        settings: BilibiliSettings,
        resolver: BilibiliWebResolver,
        downloader: BilibiliMediaDownloader,
        transcriber: AudioTranscriptionService,
        audio_extractor: FfmpegAudioExtractor,
        temp_workspaces: LocalTempWorkspaceFactory,
    ) -> None:
        """初始化B站提取器。"""

        # B站媒体限制。
        self._settings = settings
        # 公开视频解析器。
        self._resolver = resolver
        # DASH 音频下载器。
        self._downloader = downloader
        # 供应商无关的分段转录服务。
        self._transcriber = transcriber
        # 本地音频标准化器。
        self._audio_extractor = audio_extractor
        # 可配置的本地临时工作区。
        self._temp_workspaces = temp_workspaces

    # 解析、下载和转录单个B站视频分P。
    def extract(
        self,
        url: str,
        progress: ExtractionProgress | None = None,
    ) -> ExtractedContent:
        """提取B站视频元数据和完整语音转录。"""

        # 解析后的作品与音频候选清单。
        work = self._resolver.resolve(url)
        self._report(
            progress,
            "bilibili_resolved",
            {"bvid": work.bvid, "cid": work.cid, "page_number": work.page_number},
        )
        # 视频转录正文与清理警告。
        body_text, warnings = self._extract_audio(work, progress)
        return ExtractedContent(
            source_url=work.source_url,
            canonical_url=work.canonical_url,
            platform="B站",
            title=work.title,
            author=work.author,
            body_text=body_text,
            published_at=work.published_at,
            quality=ContentQuality.FULL,
            content_type=ContentType.VIDEO,
            platform_content_id=work.platform_content_id,
            warnings=warnings,
        )

    # 下载音频、标准化分段并调用已配置的 ASR。
    def _extract_audio(
        self,
        work: BilibiliWork,
        progress: ExtractionProgress | None,
    ) -> tuple[str, tuple[str, ...]]:
        """返回视频简介、完整转录和清理警告。"""

        with self._temp_workspaces.create("knowwhere-bilibili-audio-") as temp_dir:
            # 当前临时目录。
            temp_path = Path(temp_dir)
            # DASH 音频输入路径，FFmpeg 会按容器内容自动识别。
            source_audio_path = temp_path / "source-audio.m4s"
            try:
                self._downloader.download(
                    work.audio_urls,
                    source_audio_path,
                    self._settings.max_audio_bytes,
                )
            except BilibiliMediaUnavailableError:
                # 短期 CDN 地址可能过期，仅刷新一次播放信息。
                refreshed_work = self._resolver.resolve(work.source_url)
                if refreshed_work.bvid != work.bvid or refreshed_work.cid != work.cid:
                    raise ValueError("B站媒体地址刷新后作品身份发生变化") from None
                self._report(progress, "bilibili_audio_urls_refreshed", {})
                self._downloader.download(
                    refreshed_work.audio_urls,
                    source_audio_path,
                    self._settings.max_audio_bytes,
                )
            self._report(
                progress,
                "bilibili_audio_downloaded",
                {"audio_bytes": source_audio_path.stat().st_size},
            )
            # 单声道 16k 有序音频分段。
            audio_paths = self._audio_extractor.extract_segments(
                source_audio_path,
                temp_path,
            )
            self._report(
                progress,
                "bilibili_audio_extracted",
                {
                    "segment_count": len(audio_paths),
                    "audio_bytes": sum(path.stat().st_size for path in audio_paths),
                },
            )

            # 报告单个 ASR 分段完成且不暴露本地路径。
            def segment_completed(segment_index: int, segment_count: int) -> None:
                """保存当前B站转录进度。"""

                self._report(
                    progress,
                    "bilibili_asr_segment_completed",
                    {"segment_index": segment_index, "segment_count": segment_count},
                )

            # 已按顺序合并的完整视频转录。
            transcription = self._transcriber.transcribe_segments(
                audio_paths,
                segment_completed,
            )
            self._report(progress, "bilibili_asr_completed", {})
        # 视频简介与语音证据共同组成可分析正文。
        # 可检索的最终正文不包含处理分段标记。
        body_text = (
            f"视频简介：\n{work.description or '无'}\n\n视频转录：\n{transcription.text}"
        )
        return body_text, transcription.warnings

    # 可选进度回调统一空值处理。
    @staticmethod
    def _report(
        progress: ExtractionProgress | None,
        stage: str,
        data: dict[str, object],
    ) -> None:
        """报告不含临时 URL 和凭据的安全进度。"""

        if progress is not None:
            progress(stage, data)
