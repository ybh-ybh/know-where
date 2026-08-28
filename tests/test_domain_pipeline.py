"""领域状态机与应用流水线测试。"""

from __future__ import annotations

from knowwhere.adapters.fakes import (
    FakeArchive,
    FakeExtractor,
    FakeLlm,
    InMemoryTaskRepository,
    StaticCategoryCatalog,
)
from knowwhere.application.pipeline import MvpPipeline, PipelineDependencies
from knowwhere.domain.models import ContentQuality, ExtractedContent, TaskStatus


# 创建测试正文，避免每个用例重复构造领域对象。
def _content() -> ExtractedContent:
    """返回固定文章。"""

    return ExtractedContent(
        source_url="https://mp.weixin.qq.com/s/test",
        canonical_url="https://mp.weixin.qq.com/s/test",
        platform="微信公众号",
        title="测试文章",
        author="测试作者",
        body_text="正文内容" * 100,
        published_at=None,
        quality=ContentQuality.FULL,
    )


# 创建完全离线的测试流水线。
def _pipeline(
    repository: InMemoryTaskRepository,
    archive: FakeArchive | None = None,
) -> MvpPipeline:
    """组装 Fake 端口。"""

    # 可由用例复用并模拟远端删除的归档适配器。
    selected_archive = archive or FakeArchive()
    return MvpPipeline(
        PipelineDependencies(
            extractor=FakeExtractor(_content()),
            llm=FakeLlm(),
            categories=StaticCategoryCatalog(("技术与 AI", "其他")),
            archive=selected_archive,
            tasks=repository,
        )
    )


# 正常执行必须落到完成终态并保存归档索引。
def test_pipeline_completes() -> None:
    """验证端到端 Fake 闭环。"""

    # 内存任务仓储。
    repository = InMemoryTaskRepository()
    # 归档结果。
    result = _pipeline(repository).process(_content().source_url)

    assert result.provider == "fake"
    assert next(iter(repository.tasks.values())).status is TaskStatus.COMPLETED
    assert repository.archives[_content().canonical_url] == result


# 重复 URL 返回原记录时，新任务也必须进入终态。
def test_duplicate_pipeline_task_does_not_stick_in_extracting() -> None:
    """验证重复内容任务状态。"""

    # 复用同一仓储模拟第二次提交。
    repository = InMemoryTaskRepository()
    # 用于确认有效重复不会再次归档的 Fake 外部归档。
    archive = FakeArchive()
    # 离线流水线。
    pipeline = _pipeline(repository, archive)
    # 第一次归档。
    first_result = pipeline.process(_content().source_url)
    # 第二次去重。
    second_result = pipeline.process(_content().source_url)

    assert second_result == first_result
    assert len(archive.content_ids) == 1
    assert len(repository.tasks) == 2
    assert all(task.status is TaskStatus.COMPLETED for task in repository.tasks.values())


# 本地去重记录存在但外部记录已删除时，必须重新归档并刷新绑定。
def test_duplicate_pipeline_rearchives_deleted_external_record() -> None:
    """验证用户删除飞书记录后的再次提交。"""

    # 复用同一仓储模拟本地仍保存旧归档引用。
    repository = InMemoryTaskRepository()
    # 可移除记录 ID 的 Fake 外部归档。
    archive = FakeArchive()
    # 离线流水线。
    pipeline = _pipeline(repository, archive)
    # 首次归档结果。
    first_result = pipeline.process(_content().source_url)
    archive.existing_record_ids.remove(first_result.record_id)

    # 外部记录删除后的再次归档结果。
    restored_result = pipeline.process(_content().source_url)

    assert len(archive.content_ids) == 2
    assert len(set(archive.content_ids)) == 1
    assert restored_result.record_id != first_result.record_id
    assert restored_result.record_id in archive.existing_record_ids
    assert repository.archives[_content().canonical_url] == restored_result
    assert len(repository.tasks) == 2
    assert all(task.status is TaskStatus.COMPLETED for task in repository.tasks.values())
