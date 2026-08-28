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
def _pipeline(repository: InMemoryTaskRepository) -> MvpPipeline:
    """组装 Fake 端口。"""

    return MvpPipeline(
        PipelineDependencies(
            extractor=FakeExtractor(_content()),
            llm=FakeLlm(),
            categories=StaticCategoryCatalog(("技术与 AI", "其他")),
            archive=FakeArchive(),
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
    # 离线流水线。
    pipeline = _pipeline(repository)
    # 第一次归档。
    first_result = pipeline.process(_content().source_url)
    # 第二次去重。
    second_result = pipeline.process(_content().source_url)

    assert second_result == first_result
    assert len(repository.tasks) == 2
    assert all(task.status is TaskStatus.COMPLETED for task in repository.tasks.values())
