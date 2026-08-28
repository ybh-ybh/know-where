"""最小可恢复内容处理用例。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from knowwhere.application.ports import (
    CategoryCatalogPort,
    ContentExtractorPort,
    LlmProviderPort,
    RecordArchivePort,
    TaskRepositoryPort,
)
from knowwhere.domain.models import ArchiveResult, ProcessingTask, TaskStatus


# 流水线依赖集合。
@dataclass(frozen=True, slots=True)
class PipelineDependencies:
    """应用用例的端口依赖。"""

    # 内容提取器。
    extractor: ContentExtractorPort
    # LLM 分析器。
    llm: LlmProviderPort
    # 分类目录。
    categories: CategoryCatalogPort
    # 外部归档。
    archive: RecordArchivePort
    # 任务仓储。
    tasks: TaskRepositoryPort


# MVP 流水线编排器。
class MvpPipeline:
    """按状态机执行提取、分析和归档。"""

    # 保存稳定端口依赖。
    def __init__(self, dependencies: PipelineDependencies) -> None:
        """初始化流水线。"""

        self._dependencies = dependencies

    # 处理一个 URL 并返回归档引用。
    def process(self, url: str) -> ArchiveResult:
        """执行可观察的 MVP 闭环。"""

        # 新任务使用随机 UUID，外部供应商不参与身份生成。
        task = ProcessingTask(task_id=str(uuid.uuid4()), source_url=url)
        self._dependencies.tasks.create(task)
        try:
            task.transition_to(TaskStatus.EXTRACTING)
            self._dependencies.tasks.save(task)
            # 提取后的内容是后续阶段唯一事实输入。
            content = self._dependencies.extractor.extract(url)
            # 完成记录按规范 URL 去重。
            existing_archive = self._dependencies.tasks.find_completed_archive(
                content.canonical_url
            )
            if existing_archive is not None:
                # 重复提交也必须进入明确终态，不能留下伪装成运行中的任务。
                task.transition_to(TaskStatus.ANALYZING)
                self._dependencies.tasks.save(task)
                task.transition_to(TaskStatus.ARCHIVING)
                self._dependencies.tasks.save(task)
                task.transition_to(TaskStatus.COMPLETED)
                self._dependencies.tasks.save(task)
                return existing_archive

            task.transition_to(TaskStatus.ANALYZING)
            self._dependencies.tasks.save(task)
            # 分类集合在每次分析前读取，支持用户后续扩展。
            allowed_categories = self._dependencies.categories.list_categories()
            # AI 结果必须已经由适配器完成 Schema 校验或明确降级。
            analysis = self._dependencies.llm.analyze(content, allowed_categories)

            task.transition_to(TaskStatus.ARCHIVING)
            self._dependencies.tasks.save(task)
            # 正文哈希用于归档幂等和变更检测。
            content_hash = hashlib.sha256(content.body_text.encode("utf-8")).hexdigest()
            # 内容 ID 与任务 ID 分离，允许未来重新分析。
            content_id = f"cnt_{content_hash[:24]}"
            # 飞书适配器负责外部字段映射。
            archive_result = self._dependencies.archive.upsert(
                content_id,
                content_hash,
                content,
                analysis,
            )

            task.transition_to(TaskStatus.COMPLETED)
            self._dependencies.tasks.save_result(task, content, analysis, archive_result)
            return archive_result
        except Exception as error:
            # 只持久化安全错误文本，不记录配置或请求头。
            safe_message = f"{type(error).__name__}: {error}"
            task.fail(safe_message)
            self._dependencies.tasks.save(task)
            raise
