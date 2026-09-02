"""应用层依赖的稳定端口。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from knowwhere.domain.models import (
    AnalysisResult,
    ArchiveResult,
    ExtractedContent,
    ProcessingTask,
    WorkspaceBinding,
)

# 提取器通过回调持久化阶段进度，数据中不得包含凭据或临时签名 URL。
ExtractionProgress = Callable[[str, dict[str, object]], None]


# 内容提取端口。
class ContentExtractorPort(Protocol):
    """把 URL 转换为规范内容。"""

    # 提取指定地址。
    def extract(
        self,
        url: str,
        progress: ExtractionProgress | None = None,
    ) -> ExtractedContent:
        """提取并清洗正文。"""


# LLM 分析端口。
class LlmProviderPort(Protocol):
    """生成并校验分类摘要。"""

    # 分析规范内容。
    def analyze(
        self,
        content: ExtractedContent,
        allowed_categories: tuple[str, ...],
    ) -> AnalysisResult:
        """返回稳定结构化结果。"""


# 分类目录端口。
class CategoryCatalogPort(Protocol):
    """提供当前允许分类。"""

    # 读取分类集合。
    def list_categories(self) -> tuple[str, ...]:
        """返回至少一个分类。"""


# 记录归档端口。
class RecordArchivePort(Protocol):
    """把结果写入用户可见归档。"""

    # 检查已有外部记录是否仍可用。
    def archive_exists(self, archive: ArchiveResult) -> bool:
        """仅在远端记录仍存在时返回真。"""

    # 幂等写入一条记录。
    def upsert(
        self,
        content_id: str,
        content_hash: str,
        content: ExtractedContent,
        analysis: AnalysisResult,
    ) -> ArchiveResult:
        """返回外部记录引用。"""


# 工作区绑定存储端口。
class WorkspaceBindingStorePort(Protocol):
    """持久化系统创建的归档资源。"""

    # 读取指定提供方绑定。
    def get(self, provider: str) -> WorkspaceBinding | None:
        """返回现有工作区。"""

    # 保存或更新绑定。
    def put(self, binding: WorkspaceBinding) -> None:
        """持久化工作区。"""


# 任务仓储端口。
class TaskRepositoryPort(Protocol):
    """保存任务与阶段产物。"""

    # 新建任务。
    def create(self, task: ProcessingTask) -> None:
        """持久化新任务。"""

    # 保存当前状态。
    def save(self, task: ProcessingTask) -> None:
        """更新任务。"""

    # 保存阶段产物和外部引用。
    def save_result(
        self,
        task: ProcessingTask,
        content: ExtractedContent,
        analysis: AnalysisResult,
        archive: ArchiveResult,
    ) -> None:
        """原子保存完成结果。"""

    # 按规范 URL 查询已完成归档。
    def find_completed_archive(self, canonical_url: str) -> ArchiveResult | None:
        """支持内容去重。"""


# 临时对象存储端口骨架。
class ArtifactStorePort(Protocol):
    """保存可清理临时对象。"""

    # 上传字节并返回不透明引用。
    def put(self, data: bytes, suffix: str) -> str:
        """保存临时对象。"""

    # 为私有对象创建短时只读 URL。
    def create_download_url(self, artifact_ref: str) -> str:
        """返回限时下载 URL。"""

    # 删除不透明引用。
    def delete(self, artifact_ref: str) -> None:
        """幂等清理对象。"""


# ASR 端口骨架。
class AsrProviderPort(Protocol):
    """把标准音频引用转成文本。"""

    # 转录一个标准音频分段。
    def transcribe(self, artifact_url: str) -> str:
        """返回当前分段文本。"""


# 视觉模型端口只接收可短时访问的图片地址。
class VisionProviderPort(Protocol):
    """把有序图片和作品配文转换为可归档正文。"""

    # 按图片原顺序执行 OCR 和语义理解。
    def describe(self, image_urls: tuple[str, ...], caption: str) -> str:
        """返回完整图文正文。"""
