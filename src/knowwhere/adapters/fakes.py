"""单元测试和离线开发使用的 Fake 适配器。"""

from __future__ import annotations

from dataclasses import dataclass, field

from knowwhere.application.ports import (
    CategoryCatalogPort,
    ContentExtractorPort,
    LlmProviderPort,
    RecordArchivePort,
    TaskRepositoryPort,
)
from knowwhere.domain.models import (
    AnalysisResult,
    ArchiveResult,
    ContentQuality,
    ExtractedContent,
    ProcessingTask,
)


# 固定内容提取器。
@dataclass(slots=True)
class FakeExtractor(ContentExtractorPort):
    """返回预置内容。"""

    # 预置提取结果。
    content: ExtractedContent

    # 忽略 URL 返回内容。
    def extract(self, url: str) -> ExtractedContent:
        """返回预置结果。"""

        return self.content


# 固定 LLM 适配器。
class FakeLlm(LlmProviderPort):
    """生成确定性分析。"""

    # 返回合法结果。
    def analyze(
        self,
        content: ExtractedContent,
        allowed_categories: tuple[str, ...],
    ) -> AnalysisResult:
        """生成离线分析。"""

        return AnalysisResult(
            primary_category=allowed_categories[0],
            category_confidence=1.0,
            tags=("测试", "离线", "契约"),
            one_sentence_summary="离线 Fake 摘要。",
            detailed_summary="这是用于验证任务状态机和端口连接的离线 Fake 详细摘要。",
            key_points=("提取成功。", "分析成功。", "归档成功。"),
            content_quality=ContentQuality.FULL,
        )


# 固定分类目录。
@dataclass(slots=True)
class StaticCategoryCatalog(CategoryCatalogPort):
    """返回固定分类集合。"""

    # 默认分类。
    categories: tuple[str, ...]

    # 读取分类。
    def list_categories(self) -> tuple[str, ...]:
        """返回配置分类。"""

        return self.categories


# 内存归档适配器。
@dataclass(slots=True)
class FakeArchive(RecordArchivePort):
    """记录最近一次归档输入。"""

    # 已归档内容 ID。
    content_ids: list[str] = field(default_factory=list)
    # 当前仍存在的外部记录 ID。
    existing_record_ids: set[str] = field(default_factory=set)
    # 内容 ID 到最近一次 Fake 归档结果的索引。
    archives_by_content_id: dict[str, ArchiveResult] = field(default_factory=dict)

    # 检查 Fake 外部记录是否仍存在。
    def archive_exists(self, archive: ArchiveResult) -> bool:
        """按记录 ID 模拟远端存在性检查。"""

        return archive.record_id in self.existing_record_ids

    # 保存内容 ID 并返回 Fake 引用。
    def upsert(
        self,
        content_id: str,
        content_hash: str,
        content: ExtractedContent,
        analysis: AnalysisResult,
    ) -> ArchiveResult:
        """模拟外部归档。"""

        # 当前内容仍有效的 Fake 记录。
        existing_archive = self.archives_by_content_id.get(content_id)
        if existing_archive is not None and self.archive_exists(existing_archive):
            return existing_archive
        self.content_ids.append(content_id)
        # 同一内容删除重建后的递增版本号。
        generation = self.content_ids.count(content_id)
        # 确定性的 Fake 归档结果。
        archive = ArchiveResult(
            provider="fake",
            workspace_id="fake_workspace",
            record_id=f"fake_{content_id}_{generation}",
            record_url=f"https://example.test/{content_id}/{generation}",
        )
        self.existing_record_ids.add(archive.record_id)
        self.archives_by_content_id[content_id] = archive
        return archive


# 内存任务仓储。
@dataclass(slots=True)
class InMemoryTaskRepository(TaskRepositoryPort):
    """保存任务快照。"""

    # 任务字典。
    tasks: dict[str, ProcessingTask] = field(default_factory=dict)
    # URL 到归档结果的索引。
    archives: dict[str, ArchiveResult] = field(default_factory=dict)

    # 保存新任务。
    def create(self, task: ProcessingTask) -> None:
        """插入任务。"""

        self.tasks[task.task_id] = task

    # 内存对象原位更新，无需复制。
    def save(self, task: ProcessingTask) -> None:
        """更新任务。"""

        self.tasks[task.task_id] = task

    # 保存完成归档。
    def save_result(
        self,
        task: ProcessingTask,
        content: ExtractedContent,
        analysis: AnalysisResult,
        archive: ArchiveResult,
    ) -> None:
        """保存结果索引。"""

        self.tasks[task.task_id] = task
        self.archives[content.canonical_url] = archive

    # 按 URL 读取归档。
    def find_completed_archive(self, canonical_url: str) -> ArchiveResult | None:
        """查找已有归档。"""

        return self.archives.get(canonical_url)
