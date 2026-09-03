"""唯一允许选择具体供应商适配器的组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from knowwhere.adapters.article_router import ArticleExtractorRouter
from knowwhere.adapters.bilibili import (
    BilibiliContentExtractor,
    BilibiliMediaDownloader,
    BilibiliWebResolver,
)
from knowwhere.adapters.cos_artifact_store import TencentCosArtifactStore
from knowwhere.adapters.douyin import (
    DouyinContentExtractor,
    DouyinMediaDownloader,
    DouyinWebResolver,
    FfmpegAudioExtractor,
)
from knowwhere.adapters.faster_whisper_asr import FasterWhisperAsrProvider
from knowwhere.adapters.feishu_bitable import FeishuBitableAdapter
from knowwhere.adapters.github import GitHubReadmeExtractor
from knowwhere.adapters.glm_vision import GlmVisionProvider
from knowwhere.adapters.juejin import JuejinArticleExtractor
from knowwhere.adapters.llm_openai import PromptFirstOpenAiCompatibleLlm
from knowwhere.adapters.local_temp_storage import LocalTempWorkspaceFactory
from knowwhere.adapters.tencent_asr import TencentFileAsrProvider
from knowwhere.adapters.wechat import WeChatArticleExtractor
from knowwhere.adapters.xiaohongshu import (
    XiaohongshuContentExtractor,
    XiaohongshuMediaDownloader,
    XiaohongshuWebResolver,
)
from knowwhere.application.media import AudioTranscriptionService
from knowwhere.application.pipeline import MvpPipeline, PipelineDependencies
from knowwhere.application.ports import AsrProviderPort
from knowwhere.config import AppSettings
from knowwhere.infrastructure.database import Database
from knowwhere.infrastructure.repositories import SqlTaskRepository, SqlWorkspaceBindingStore


# 运行时对象集合让 CLI 和未来 Gateway 共用同一组端口装配。
@dataclass(frozen=True, slots=True)
class Runtime:
    """知归 MVP 的运行时依赖。"""

    # 已校验配置。
    settings: AppSettings
    # 数据库连接。
    database: Database
    # 处理流水线。
    pipeline: MvpPipeline
    # 飞书归档适配器。
    feishu: FeishuBitableAdapter


# 创建生产适配器并注入应用用例。
def build_runtime(config_path: Path | None = None) -> Runtime:
    """从配置创建完整 MVP 运行时。"""

    # 经 Pydantic 校验后的配置。
    settings = AppSettings.load(config_path)
    # 唯一启用的 OpenAI 兼容 LLM 配置。
    llm_settings = settings.llm
    # PostgreSQL 基础设施。
    database = Database(settings.database_url)
    # 任务事实仓储。
    task_repository = SqlTaskRepository(database)
    # 飞书资源绑定仓储。
    binding_store = SqlWorkspaceBindingStore(database)
    # 飞书同时提供分类集合与归档写入。
    feishu = FeishuBitableAdapter(
        settings.feishu,
        binding_store,
        model_info=f"openai_compatible/{llm_settings.model}; prompt=v1",
    )
    # OpenAI 兼容 LLM 适配器。
    llm = PromptFirstOpenAiCompatibleLlm("openai_compatible", llm_settings)
    # 微信公众号正文提取器。
    wechat_extractor = WeChatArticleExtractor()
    # 稀土掘金正文提取器。
    juejin_extractor = JuejinArticleExtractor()
    # GitHub 仓库 README 提取器。
    github_extractor = GitHubReadmeExtractor()
    # 主机名到内容提取器的注册表。
    extractors = {
        "mp.weixin.qq.com": wechat_extractor,
        "juejin.cn": juejin_extractor,
        "www.juejin.cn": juejin_extractor,
        "github.com": github_extractor,
        "www.github.com": github_extractor,
    }
    # 可配置的本地下载和 FFmpeg 工作目录。
    temp_workspaces = LocalTempWorkspaceFactory(settings.temp_storage)
    # 当前配置选择的供应商无关 ASR 端口。
    asr: AsrProviderPort
    if settings.asr_provider == "tencent":
        if settings.tencentcloud is None:
            raise ValueError("腾讯 ASR 缺少已校验的腾讯云配置")
        # 私有 COS 临时对象存储。
        artifact_store = TencentCosArtifactStore(settings.tencentcloud)
        # 腾讯云录音文件识别内部独占 COS 中转逻辑。
        asr = TencentFileAsrProvider(
            settings.tencentcloud,
            artifact_store,
            delete_after_process=settings.temp_storage.delete_after_process,
        )
    else:
        # 本地实现延迟加载模型且不构造任何腾讯客户端。
        asr = FasterWhisperAsrProvider(settings.faster_whisper)
    # 三个平台共用的音频分段转录编排。
    transcriber = AudioTranscriptionService(asr)
    # 图文视觉模型在未配置时由平台提取器给出精确错误。
    vision = GlmVisionProvider(settings.vision) if settings.vision is not None else None
    # 抖音公开图文与视频提取器。
    douyin_extractor = DouyinContentExtractor(
        settings=settings.douyin,
        resolver=DouyinWebResolver(settings.douyin),
        downloader=DouyinMediaDownloader(settings.douyin),
        transcriber=transcriber,
        vision=vision,
        audio_extractor=FfmpegAudioExtractor(settings.douyin.ffmpeg_path),
        temp_workspaces=temp_workspaces,
    )
    # B站公开视频提取器直接下载 DASH 音频并复用统一转录服务。
    bilibili_extractor = BilibiliContentExtractor(
        settings=settings.bilibili,
        resolver=BilibiliWebResolver(settings.bilibili),
        downloader=BilibiliMediaDownloader(settings.bilibili),
        transcriber=transcriber,
        audio_extractor=FfmpegAudioExtractor(settings.bilibili.ffmpeg_path),
        temp_workspaces=temp_workspaces,
    )
    # 小红书图文和视频复用视觉、FFmpeg 与统一 ASR 能力。
    xiaohongshu_extractor = XiaohongshuContentExtractor(
        settings=settings.xiaohongshu,
        resolver=XiaohongshuWebResolver(settings.xiaohongshu),
        downloader=XiaohongshuMediaDownloader(settings.xiaohongshu),
        transcriber=transcriber,
        vision=vision,
        audio_extractor=FfmpegAudioExtractor(settings.xiaohongshu.ffmpeg_path),
        temp_workspaces=temp_workspaces,
    )
    extractors.update(
        {
            "v.douyin.com": douyin_extractor,
            "www.iesdouyin.com": douyin_extractor,
            "www.douyin.com": douyin_extractor,
            "bilibili.com": bilibili_extractor,
            "www.bilibili.com": bilibili_extractor,
            "m.bilibili.com": bilibili_extractor,
            "b23.tv": bilibili_extractor,
            "xiaohongshu.com": xiaohongshu_extractor,
            "www.xiaohongshu.com": xiaohongshu_extractor,
            "xhslink.cn": xiaohongshu_extractor,
            "xhslink.com": xiaohongshu_extractor,
        }
    )
    # 内容平台分派器是应用层看到的唯一内容提取端口。
    extractor = ArticleExtractorRouter(extractors)
    # 端口依赖集合。
    dependencies = PipelineDependencies(
        extractor=extractor,
        llm=llm,
        categories=feishu,
        archive=feishu,
        tasks=task_repository,
    )
    return Runtime(
        settings=settings,
        database=database,
        pipeline=MvpPipeline(dependencies),
        feishu=feishu,
    )
