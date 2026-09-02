"""PostgreSQL 仓储适配器。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from knowwhere.application.ports import TaskRepositoryPort, WorkspaceBindingStorePort
from knowwhere.domain.models import (
    AnalysisResult,
    ArchiveResult,
    ExtractedContent,
    ProcessingTask,
    WorkspaceBinding,
)
from knowwhere.infrastructure.database import (
    ArchiveWorkspaceRow,
    ContentRow,
    Database,
    TaskRow,
)


# PostgreSQL 任务仓储。
class SqlTaskRepository(TaskRepositoryPort):
    """保存任务和完成内容。"""

    # 保存数据库依赖。
    def __init__(self, database: Database) -> None:
        """初始化仓储。"""

        self._database = database

    # 插入新任务。
    def create(self, task: ProcessingTask) -> None:
        """持久化新任务。"""

        # ORM 任务行。
        row = TaskRow(
            task_id=task.task_id,
            source_url=task.source_url,
            status=task.status.value,
            error_message=task.error_message,
            checkpoint_stage=task.checkpoint_stage,
            checkpoint_data=task.checkpoint_data,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        with self._database.session_factory.begin() as session:
            session.add(row)

    # 更新任务当前状态。
    def save(self, task: ProcessingTask) -> None:
        """更新任务。"""

        with self._database.session_factory.begin() as session:
            # 当前数据库行。
            row = session.get(TaskRow, task.task_id)
            if row is None:
                raise LookupError(f"任务不存在: {task.task_id}")
            row.status = task.status.value
            row.error_message = task.error_message
            row.checkpoint_stage = task.checkpoint_stage
            row.checkpoint_data = task.checkpoint_data
            row.updated_at = task.updated_at

    # 原子保存完成任务和内容结果。
    def save_result(
        self,
        task: ProcessingTask,
        content: ExtractedContent,
        analysis: AnalysisResult,
        archive: ArchiveResult,
    ) -> None:
        """提交完成状态和归档绑定。"""

        # 正文哈希与流水线一致。
        import hashlib

        # 计算内容哈希。
        content_hash = hashlib.sha256(content.body_text.encode("utf-8")).hexdigest()
        # 稳定内容 ID。
        content_id = f"cnt_{content_hash[:24]}"
        # 可 JSON 化分析结果。
        analysis_data = {
            "primary_category": analysis.primary_category,
            "category_confidence": analysis.category_confidence,
            "tags": list(analysis.tags),
            "one_sentence_summary": analysis.one_sentence_summary,
            "detailed_summary": analysis.detailed_summary,
            "key_points": list(analysis.key_points),
            "content_quality": analysis.content_quality.value,
            "warnings": list(analysis.warnings),
            "degraded": analysis.degraded,
        }
        with self._database.session_factory.begin() as session:
            # 当前任务行。
            task_row = session.get(TaskRow, task.task_id)
            if task_row is None:
                raise LookupError(f"任务不存在: {task.task_id}")
            task_row.status = task.status.value
            task_row.error_message = task.error_message
            task_row.checkpoint_stage = task.checkpoint_stage
            task_row.checkpoint_data = task.checkpoint_data
            task_row.updated_at = task.updated_at
            # 同 URL 内容只保留一个内部事实记录。
            content_row = session.scalar(
                select(ContentRow).where(ContentRow.canonical_url == content.canonical_url)
            )
            if content_row is None:
                content_row = ContentRow(
                    content_id=content_id,
                    canonical_url=content.canonical_url,
                    title=content.title,
                    platform=content.platform,
                    platform_content_id=content.platform_content_id,
                    content_type=content.content_type.value,
                    author=content.author,
                    published_at=content.published_at,
                    body_text=content.body_text,
                    content_hash=content_hash,
                    analysis=analysis_data,
                    archive_provider=archive.provider,
                    workspace_id=archive.workspace_id,
                    record_id=archive.record_id,
                    record_url=archive.record_url,
                    completed_at=datetime.now(UTC),
                )
                session.add(content_row)
            else:
                content_row.title = content.title
                content_row.platform = content.platform
                content_row.platform_content_id = content.platform_content_id
                content_row.content_type = content.content_type.value
                content_row.author = content.author
                content_row.published_at = content.published_at
                content_row.body_text = content.body_text
                content_row.content_hash = content_hash
                content_row.analysis = analysis_data
                content_row.archive_provider = archive.provider
                content_row.workspace_id = archive.workspace_id
                content_row.record_id = archive.record_id
                content_row.record_url = archive.record_url
                content_row.completed_at = datetime.now(UTC)

    # 查找已完成归档。
    def find_completed_archive(self, canonical_url: str) -> ArchiveResult | None:
        """按规范 URL 去重。"""

        with self._database.session_factory() as session:
            # 已完成内容行。
            row = session.scalar(
                select(ContentRow).where(ContentRow.canonical_url == canonical_url)
            )
            if row is None:
                return None
            return ArchiveResult(
                provider=row.archive_provider,
                workspace_id=row.workspace_id,
                record_id=row.record_id,
                record_url=row.record_url,
            )


# PostgreSQL 工作区绑定存储。
class SqlWorkspaceBindingStore(WorkspaceBindingStorePort):
    """保存飞书工作区资源 ID。"""

    # 保存数据库依赖。
    def __init__(self, database: Database) -> None:
        """初始化存储。"""

        self._database = database

    # 读取现有绑定。
    def get(self, provider: str) -> WorkspaceBinding | None:
        """读取工作区。"""

        with self._database.session_factory() as session:
            # 工作区数据库行。
            row = session.get(ArchiveWorkspaceRow, provider)
            if row is None:
                return None
            return WorkspaceBinding(
                provider=row.provider,
                workspace_id=row.workspace_id,
                table_id=row.table_id,
                primary_field_name=row.primary_field_name,
                workspace_url=row.workspace_url,
                schema_version=row.schema_version,
            )

    # 幂等保存工作区绑定。
    def put(self, binding: WorkspaceBinding) -> None:
        """Upsert 工作区。"""

        with self._database.session_factory.begin() as session:
            # 当前工作区行。
            row = session.get(ArchiveWorkspaceRow, binding.provider)
            if row is None:
                row = ArchiveWorkspaceRow(
                    provider=binding.provider,
                    workspace_id=binding.workspace_id,
                    table_id=binding.table_id,
                    primary_field_name=binding.primary_field_name,
                    workspace_url=binding.workspace_url,
                    schema_version=binding.schema_version,
                )
                session.add(row)
            else:
                row.workspace_id = binding.workspace_id
                row.table_id = binding.table_id
                row.primary_field_name = binding.primary_field_name
                row.workspace_url = binding.workspace_url
                row.schema_version = binding.schema_version
