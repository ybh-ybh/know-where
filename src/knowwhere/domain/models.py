"""供应商无关的领域数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar


# 内容质量枚举只表达证据完整度。
class ContentQuality(StrEnum):
    """内容证据完整度。"""

    # 已取得完整正文或转录。
    FULL = "full"
    # 仅取得部分正文。
    PARTIAL = "partial"
    # 仅取得元数据。
    METADATA_ONLY = "metadata_only"


# 内容类型用于统一文章、图文与视频的入库语义。
class ContentType(StrEnum):
    """归档内容类型。"""

    # 普通网页文章。
    ARTICLE = "article"
    # 多张图片与配文组成的图文作品。
    IMAGE_TEXT = "image_text"
    # 以音视频转录为主要证据的视频作品。
    VIDEO = "video"


# 任务状态枚举表达可持久化阶段。
class TaskStatus(StrEnum):
    """处理任务状态。"""

    # 等待 Worker 领取。
    QUEUED = "queued"
    # 正在提取正文。
    EXTRACTING = "extracting"
    # 正在调用模型分析。
    ANALYZING = "analyzing"
    # 正在写入归档。
    ARCHIVING = "archiving"
    # 全部阶段完成。
    COMPLETED = "completed"
    # 存在可见失败。
    FAILED = "failed"


# 提取结果保持与平台 SDK 解耦。
@dataclass(frozen=True, slots=True)
class ExtractedContent:
    """从来源页面提取的规范内容。"""

    # 原始访问地址。
    source_url: str
    # 规范化后的地址。
    canonical_url: str
    # 内容平台标识。
    platform: str
    # 文章标题。
    title: str
    # 作者名称。
    author: str | None
    # 清洗后的完整正文。
    body_text: str
    # 来源发布时间。
    published_at: datetime | None
    # 内容质量。
    quality: ContentQuality
    # 归档内容类型；默认值保持现有文章提取器兼容。
    content_type: ContentType = ContentType.ARTICLE
    # 平台作品或文章 ID，用于跨链接形式去重。
    platform_content_id: str | None = None
    # 提取警告。
    warnings: tuple[str, ...] = ()


# AI 分析结果是归档所需的稳定结构。
@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """分类与摘要结果。"""

    # 一级分类。
    primary_category: str
    # 分类置信度。
    category_confidence: float
    # 规范标签。
    tags: tuple[str, ...]
    # 一句话摘要。
    one_sentence_summary: str
    # 详细摘要。
    detailed_summary: str
    # 关键观点。
    key_points: tuple[str, ...]
    # 内容质量。
    content_quality: ContentQuality
    # 降级警告。
    warnings: tuple[str, ...] = ()
    # 是否使用启发式降级。
    degraded: bool = False


# 归档结果只保存稳定外部引用。
@dataclass(frozen=True, slots=True)
class ArchiveResult:
    """外部归档引用。"""

    # 归档提供方。
    provider: str
    # 工作区外部标识。
    workspace_id: str
    # 记录外部标识。
    record_id: str
    # 用户可访问链接。
    record_url: str


# 归档工作区绑定保存飞书资源 ID。
@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    """系统管理的归档工作区。"""

    # 归档提供方。
    provider: str
    # 外部工作区标识。
    workspace_id: str
    # 外部数据表标识。
    table_id: str
    # 主字段名称。
    primary_field_name: str
    # 用户可访问工作区链接。
    workspace_url: str
    # Schema 版本。
    schema_version: int


# 任务对象负责状态迁移约束。
@dataclass(slots=True)
class ProcessingTask:
    """可持久化处理任务。"""

    # 内部任务标识。
    task_id: str
    # 原始 URL。
    source_url: str
    # 当前状态。
    status: TaskStatus = TaskStatus.QUEUED
    # 当前失败信息。
    error_message: str | None = None
    # 提取阶段中最近完成的检查点。
    checkpoint_stage: str | None = None
    # 检查点的无密钥结构化数据。
    checkpoint_data: dict[str, object] = field(default_factory=dict)
    # 创建时间。
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # 更新时间。
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # 合法状态迁移表。
    _TRANSITIONS: ClassVar[dict[TaskStatus, set[TaskStatus]]] = {
        TaskStatus.QUEUED: {TaskStatus.EXTRACTING, TaskStatus.FAILED},
        TaskStatus.EXTRACTING: {TaskStatus.ANALYZING, TaskStatus.FAILED},
        TaskStatus.ANALYZING: {TaskStatus.ARCHIVING, TaskStatus.FAILED},
        TaskStatus.ARCHIVING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
        TaskStatus.COMPLETED: set(),
        TaskStatus.FAILED: {TaskStatus.EXTRACTING},
    }

    # 将任务推进到合法目标状态。
    def transition_to(self, target: TaskStatus) -> None:
        """校验并执行状态迁移。"""

        # 当前状态允许的目标集合。
        allowed_targets = self._TRANSITIONS[self.status]
        if target not in allowed_targets:
            raise ValueError(f"非法任务迁移: {self.status} -> {target}")
        self.status = target
        self.updated_at = datetime.now(UTC)
        if target is not TaskStatus.FAILED:
            self.error_message = None

    # 将任务置为失败并保存安全错误。
    def fail(self, message: str) -> None:
        """记录任务失败。"""

        if self.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            raise ValueError(f"终态任务不能再次失败: {self.status}")
        self.status = TaskStatus.FAILED
        self.error_message = message[:1000]
        self.updated_at = datetime.now(UTC)

    # 保存可持久化的提取进度。
    def checkpoint(self, stage: str, data: dict[str, object] | None = None) -> None:
        """记录最近完成阶段及其安全元数据。"""

        self.checkpoint_stage = stage
        self.checkpoint_data = dict(data or {})
        self.updated_at = datetime.now(UTC)
