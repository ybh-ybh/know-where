"""知归命令行入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from knowwhere.adapters.fakes import (
    FakeArchive,
    FakeExtractor,
    FakeLlm,
    InMemoryTaskRepository,
    StaticCategoryCatalog,
)
from knowwhere.application.pipeline import MvpPipeline, PipelineDependencies
from knowwhere.composition import build_runtime
from knowwhere.config import AppSettings
from knowwhere.domain.models import ContentQuality, ExtractedContent
from knowwhere.gateway import run_gateway

# Typer 应用对象。
app = typer.Typer(no_args_is_help=True, help="知归（KnowWhere）MVP 管理命令")


# 构造并运行 Alembic 到最新版本。
def _upgrade_database(settings: AppSettings) -> None:
    """升级 PostgreSQL Schema。"""

    # Alembic 配置对象。
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_config, "head")


# 校验配置和数据库连接。
@app.command("health")
def health(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """验证本地配置与 PostgreSQL。"""

    # 完整运行时只在本地构造，不会调用云端生成接口。
    runtime = build_runtime(env_file)
    with runtime.database.engine.connect() as connection:
        # 最小数据库探针。
        connection.execute(text("SELECT 1"))
    typer.echo("OK: config + postgresql")


# 升级数据库后执行健康检查，供 Docker Compose 使用。
@app.command("migrate-and-health")
def migrate_and_health(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """迁移数据库并验证连接。"""

    # 经校验的运行配置。
    settings = AppSettings.load(env_file)
    _upgrade_database(settings)
    # 完整运行时数据库探针。
    runtime = build_runtime(env_file)
    with runtime.database.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    typer.echo("OK: migrations + config + postgresql")


# 初始化或迁移飞书多维表格。
@app.command("init-feishu")
def init_feishu(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """系统创建“知归”多维表格并输出访问链接。"""

    # 当前生产运行时。
    runtime = build_runtime(env_file)
    # 确保数据库先具备绑定表。
    _upgrade_database(runtime.settings)
    # 幂等初始化飞书资源。
    binding = runtime.feishu.ensure_workspace()
    typer.echo(f"workspace_url={binding.workspace_url}")
    typer.echo(f"schema_version={binding.schema_version}")


# 运行指定 URL 的真实提取、AI 和飞书归档流程。
@app.command("process")
def process(
    url: Annotated[str, typer.Argument(help="公开微信公众号文章 HTTPS 链接")],
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """处理一篇文章并输出飞书记录链接。"""

    # 当前生产运行时。
    runtime = build_runtime(env_file)
    # CLI 模式自动迁移，降低首次运行步骤遗漏概率。
    _upgrade_database(runtime.settings)
    # 端到端处理结果。
    result = runtime.pipeline.process(url)
    typer.echo(f"record_url={result.record_url}")
    typer.echo(f"record_id={result.record_id}")


# 运行飞书官方 SDK 长连接入口。
@app.command("gateway")
def gateway(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """监听个人用户发送给机器人的微信公众号链接。"""

    # 当前生产运行时。
    runtime = build_runtime(env_file)
    # 长连接启动前必须完成内部表迁移。
    _upgrade_database(runtime.settings)
    typer.echo("知归飞书长连接已启动")
    run_gateway(runtime)


# 运行不访问网络和数据库的端口接线冒烟测试。
@app.command("fake-smoke")
def fake_smoke() -> None:
    """验证端口、Fake 和状态机骨架。"""

    # 离线测试正文。
    content = ExtractedContent(
        source_url="https://mp.weixin.qq.com/s/fake",
        canonical_url="https://mp.weixin.qq.com/s/fake",
        platform="微信公众号",
        title="离线冒烟测试",
        author="知归",
        body_text="这是离线正文。" * 100,
        published_at=None,
        quality=ContentQuality.FULL,
    )
    # 内存仓储用于观察终态。
    repository = InMemoryTaskRepository()
    # 离线流水线。
    pipeline = MvpPipeline(
        PipelineDependencies(
            extractor=FakeExtractor(content),
            llm=FakeLlm(),
            categories=StaticCategoryCatalog(("技术与 AI", "其他")),
            archive=FakeArchive(),
            tasks=repository,
        )
    )
    # 归档结果。
    result = pipeline.process(content.source_url)
    # 最新任务状态。
    latest_task = next(iter(repository.tasks.values()))
    typer.echo(f"status={latest_task.status.value}")
    typer.echo(f"record_url={result.record_url}")


if __name__ == "__main__":
    app()
